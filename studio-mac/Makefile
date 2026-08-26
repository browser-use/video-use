APP_NAME  = video-use Studio
BUILD_DIR = .build
RELEASE   = $(BUILD_DIR)/release/Studio
BUNDLE    = $(BUILD_DIR)/$(APP_NAME).app

.PHONY: build app run clean

build:
	swift build -c release

app: build
	rm -rf "$(BUNDLE)"
	mkdir -p "$(BUNDLE)/Contents/MacOS"
	mkdir -p "$(BUNDLE)/Contents/Resources"
	cp "$(RELEASE)" "$(BUNDLE)/Contents/MacOS/$(APP_NAME)"
	cp Info.plist "$(BUNDLE)/Contents/Info.plist"
	@echo "Assembled $(BUNDLE)"

run: app
	open "$(BUNDLE)"

clean:
	rm -rf $(BUILD_DIR)
