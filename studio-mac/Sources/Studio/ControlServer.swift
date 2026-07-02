import Foundation

// Minimal localhost HTTP/1.0 control server so agents can drive the UI — no third-party deps.
//   GET  /state                    -> {"edlPath","slices","selection","playing","playhead"}
//   POST /cmd  {"op": ...}         -> ops open/toggle/play/pause/seek/select/undo/redo/reload/export
// Port 4860 (the Tauri app owns 4859). One connection at a time is plenty for agent control.
final class ControlServer {
    private let port: UInt16
    private let stateProvider: () -> String   // invoked on the main queue
    private let command: (String) -> Void     // invoked on the main queue
    private var listenFD: Int32 = -1

    init(port: UInt16, stateProvider: @escaping () -> String, command: @escaping (String) -> Void) {
        self.port = port
        self.stateProvider = stateProvider
        self.command = command
    }

    func start() {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { NSLog("studio: socket() failed"); return }
        var yes: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")

        let bound = withUnsafePointer(to: &addr) { p in
            p.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        guard bound == 0 else {
            NSLog("studio: control server cannot bind 127.0.0.1:\(port) (in use?)")
            close(fd)
            return
        }
        guard listen(fd, 16) == 0 else { NSLog("studio: listen() failed"); close(fd); return }
        listenFD = fd

        Thread.detachNewThread { [weak self] in self?.acceptLoop() }
    }

    private func acceptLoop() {
        while true {
            let conn = accept(listenFD, nil, nil)
            if conn < 0 { continue }
            handle(conn)
            close(conn)
        }
    }

    private func handle(_ conn: Int32) {
        guard let request = readRequest(conn) else { return }
        let (method, path, body) = request

        if method == "GET" && path.hasPrefix("/state") {
            var state = "{}"
            DispatchQueue.main.sync { state = self.stateProvider() }
            respond(conn, code: 200, body: state)
        } else if method == "POST" && path.hasPrefix("/cmd") {
            DispatchQueue.main.async { self.command(body) }
            respond(conn, code: 200, body: #"{"ok":true}"#)
        } else if method == "OPTIONS" {
            respond(conn, code: 200, body: "")
        } else {
            respond(conn, code: 404, body: #"{"error":"use GET /state or POST /cmd {\"op\":...}"}"#)
        }
    }

    /// Read headers, then Content-Length bytes of body. Returns (method, path, body).
    private func readRequest(_ conn: Int32) -> (String, String, String)? {
        var buffer = Data()
        var headerEnd: Data.Index?
        let bufSize = 4096
        var chunk = [UInt8](repeating: 0, count: bufSize)

        while headerEnd == nil {
            let n = read(conn, &chunk, bufSize)
            if n <= 0 { return nil }
            buffer.append(contentsOf: chunk[0..<n])
            headerEnd = buffer.range(of: Data("\r\n\r\n".utf8))?.lowerBound
            if buffer.count > 1_000_000 { return nil }
        }
        guard let hEnd = headerEnd else { return nil }
        let headerData = buffer[..<hEnd]
        guard let headerStr = String(data: headerData, encoding: .utf8) else { return nil }
        let lines = headerStr.split(separator: "\r\n", omittingEmptySubsequences: false)
        guard let requestLine = lines.first else { return nil }
        let parts = requestLine.split(separator: " ")
        guard parts.count >= 2 else { return nil }
        let method = String(parts[0])
        let path = String(parts[1])

        var contentLength = 0
        for line in lines.dropFirst() {
            let lower = line.lowercased()
            if lower.hasPrefix("content-length:") {
                contentLength = Int(line.split(separator: ":")[1].trimmingCharacters(in: .whitespaces)) ?? 0
            }
        }

        let bodyStart = buffer.index(hEnd, offsetBy: 4)
        var body = Data(buffer[bodyStart...])
        while body.count < contentLength {
            let n = read(conn, &chunk, bufSize)
            if n <= 0 { break }
            body.append(contentsOf: chunk[0..<n])
        }
        return (method, path, String(data: body, encoding: .utf8) ?? "")
    }

    private func respond(_ conn: Int32, code: Int, body: String) {
        let status = code == 200 ? "200 OK" : (code == 404 ? "404 Not Found" : "500 Internal Server Error")
        let bodyData = Data(body.utf8)
        let head = """
        HTTP/1.0 \(status)\r
        Content-Type: application/json\r
        Access-Control-Allow-Origin: *\r
        Access-Control-Allow-Headers: *\r
        Content-Length: \(bodyData.count)\r
        Connection: close\r
        \r

        """
        var out = Data(head.utf8)
        out.append(bodyData)
        out.withUnsafeBytes { raw in
            _ = write(conn, raw.baseAddress, raw.count)
        }
    }
}
