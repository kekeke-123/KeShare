# KeShare for iOS (arm64) -- 直接用 clang / swiftc 构建，无需 Xcode 工程文件
#
#   make            构建 KeShare.app（含灵动岛扩展）
#   make ipa        打包成 KeShare.ipa（未签名）
#   make clean
#
# 需要：macOS + Xcode 命令行工具（iPhoneOS SDK、clang、swiftc、ld）

APP        := KeShare
EXT        := KeShareWidget
VERSION    := 1.0.0
BUILD      := 1

MIN_IOS    := 16.1
ARCH       := arm64
TARGET     := arm64-apple-ios$(MIN_IOS)

SDK        := $(shell xcrun --sdk iphoneos --show-sdk-path)
CLANG      := xcrun --sdk iphoneos clang
SWIFTC     := xcrun --sdk iphoneos swiftc

OUT        := build
APP_DIR    := $(OUT)/$(APP).app
EXT_DIR    := $(APP_DIR)/PlugIns/$(EXT).appex

SRC_C      := ../protocol/ks_ke1024.c
SRC_M      := main.m KSApp.m KSStore.m KSInternal.m KSNet.m KSNetData.m KSSession.m KSUI.m KSActivityShim.m
SRC_SWIFT  := KSActivityManager.swift

OBJ        := $(patsubst %.c,$(OUT)/%.o,$(notdir $(SRC_C))) \
              $(patsubst %.m,$(OUT)/%.o,$(SRC_M))

CFLAGS     := -target $(TARGET) -isysroot $(SDK) -O2 -fobjc-arc -fmodules \
              -Wall -Wno-unused-parameter -Wno-deprecated-declarations \
              -I. -I../protocol
SWIFTFLAGS := -target $(TARGET) -sdk $(SDK) -O -whole-module-optimization \
              -parse-as-library
LDFLAGS    := -target $(TARGET) -isysroot $(SDK) \
              -framework UIKit -framework Foundation -framework CoreBluetooth \
              -framework CoreGraphics -framework QuartzCore -framework Security \
              -framework Network -framework ActivityKit -framework WidgetKit \
              -framework SwiftUI -framework UniformTypeIdentifiers

.PHONY: all app sign ipa clean

all: app

$(OUT):
	@mkdir -p $(OUT)

$(OUT)/%.o: %.m | $(OUT)
	$(CLANG) $(CFLAGS) -c $< -o $@

$(OUT)/%.o: ../protocol/%.c | $(OUT)
	$(CLANG) $(CFLAGS) -std=c99 -c $< -o $@

# ---------- 主 App ----------
app: $(APP_DIR)/$(APP) $(EXT_DIR)/$(EXT)

$(APP_DIR)/$(APP): $(OBJ) Info.plist
	@mkdir -p $(APP_DIR)
	$(SWIFTC) $(SWIFTFLAGS) -emit-object -o $(OUT)/KSActivityManager.o $(SRC_SWIFT) Widget/KeShareActivityAttributes.swift
	# 用 swiftc 作为链接驱动：它会自动带上 Swift 运行时与 rpath
	$(SWIFTC) -target $(TARGET) -sdk $(SDK) -O \
		$(OBJ) $(OUT)/KSActivityManager.o \
		-framework UIKit -framework Foundation -framework CoreBluetooth \
		-framework CoreGraphics -framework QuartzCore -framework Security \
		-framework Network -framework ActivityKit -framework WidgetKit \
		-framework SwiftUI -framework UniformTypeIdentifiers \
		-Xlinker -rpath -Xlinker /usr/lib/swift \
		-o $(APP_DIR)/$(APP)
	@cp Info.plist $(APP_DIR)/Info.plist
	@plutil -replace CFBundleShortVersionString -string "$(VERSION)" $(APP_DIR)/Info.plist
	@plutil -replace CFBundleVersion -string "$(BUILD)" $(APP_DIR)/Info.plist
	@echo "built: $(APP_DIR)"

# ---------- 灵动岛 / 实时活动扩展 ----------
$(EXT_DIR)/$(EXT): Widget/KeShareActivityAttributes.swift Widget/KeShareWidgetBundle.swift Widget/Info.plist
	@mkdir -p $(EXT_DIR)
	$(SWIFTC) $(SWIFTFLAGS) \
		-module-name $(EXT) \
		-framework WidgetKit -framework SwiftUI -framework ActivityKit \
		-o $(EXT_DIR)/$(EXT) \
		Widget/KeShareActivityAttributes.swift Widget/KeShareWidgetBundle.swift
	@cp Widget/Info.plist $(EXT_DIR)/Info.plist
	@plutil -replace CFBundleShortVersionString -string "$(VERSION)" $(EXT_DIR)/Info.plist
	@plutil -replace CFBundleVersion -string "$(BUILD)" $(EXT_DIR)/Info.plist
	@echo "built: $(EXT_DIR)"

# 伪签名（越狱设备安装必需）。没有 ldid 时跳过并提示。
LDID ?= $(shell command -v ldid 2>/dev/null || echo /tmp/ldid)

sign: app
	@if [ -x "$(LDID)" ]; then \
		$(LDID) -S$(PWD)/entitlements.plist $(EXT_DIR)/$(EXT) && \
		$(LDID) -S$(PWD)/entitlements.plist $(APP_DIR)/$(APP) && \
		echo "signed: $(APP_DIR)"; \
	else \
		echo "!! 未找到 ldid，IPA 将保持未签名（越狱机能装，普通设备装不上）"; \
	fi

ipa: sign
	@rm -rf $(OUT)/Payload $(OUT)/$(APP).ipa
	@mkdir -p $(OUT)/Payload
	@cp -R $(APP_DIR) $(OUT)/Payload/
	@cd $(OUT) && zip -qry $(APP).ipa Payload
	@echo "built: $(OUT)/$(APP).ipa"

clean:
	@rm -rf $(OUT)


