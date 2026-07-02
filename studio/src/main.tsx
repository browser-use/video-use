import ReactDOM from "react-dom/client";
import App from "./App";
import "./theme.css";
import "./App.css";

// No StrictMode: dev double-rendering halves headroom for the 60fps-adjacent
// playback path, and async effect cleanups are already handled explicitly.
ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(<App />);
