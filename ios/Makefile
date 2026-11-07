# KeShare for iOS (arm64) -- 直接用 clang / swiftc 构建，无需 Xcode 工程文件
#
#   make            构建 KeShare.app（含灵动岛扩展）
#   make ipa        打包成 KeShare.ipa（未签名）
#   make clean
#
# 需要：macOS + Xcode 命令行工具（iPhoneOS SDK、clang、swiftc、ld）

APP        := KeShare
VERSION    := 1.0.1
BUILD      := 1

MIN_IOS    := 15.0
ARCH       := arm64
TARGET     := arm64-apple-ios$(MIN_IOS)

SDK        := $(shell xcrun --sdk iphoneos --show-sdk-path)
CLANG      := xcrun --sdk iphoneos clang
SWIFTC     := xcrun --sdk iphoneos swiftc

OUT        := build
APP_DIR    := $(OUT)/$(APP).app

SRC_C      :=
SRC_M      := main.m KSApp.m KSStore.m KSInternal.m KSDisc.m KSNet.m KSNetData.m KSNetServe.m KSSession.m KSWiFi.m KSDevGate.m KSUI.m KSActivityShim.m
SRC_SWIFT  :=

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
              -framework Network -framework UniformTypeIdentifiers \
              -framework NetworkExtension -framework SystemConfiguration

.PHONY: all app sign ipa clean

all: app

$(OUT):
	@mkdir -p $(OUT)

$(OUT)/%.o: %.m | $(OUT)
	$(CLANG) $(CFLAGS) -c $< -o $@

$(OUT)/%.o: ../protocol/%.c | $(OUT)
	$(CLANG) $(CFLAGS) -std=c99 -c $< -o $@

# ---------- 主 App ----------
app: $(APP_DIR)/$(APP)

$(APP_DIR)/$(APP): $(OBJ) Info.plist
	@mkdir -p $(APP_DIR)
	$(CLANG) $(LDFLAGS) $(OBJ) -o $(APP_DIR)/$(APP)
	@cp Info.plist $(APP_DIR)/Info.plist
	@plutil -replace CFBundleShortVersionString -string "$(VERSION)" $(APP_DIR)/Info.plist
	@plutil -replace CFBundleVersion -string "$(BUILD)" $(APP_DIR)/Info.plist
	@echo "built: $(APP_DIR)"

# 伪签名（越狱设备安装必需）。没有 ldid 时跳过并提示。
LDID ?= $(shell command -v ldid 2>/dev/null || echo /tmp/ldid)

sign: app
	@if [ -x "$(LDID)" ]; then \
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

# 兼容版：去掉 PlugIns（灵动岛扩展），供第三方签名工具使用
# 未越狱设备必须用证书重签，很多工具处理不了 appex，这个版本最稳。
ipa-noext: app
	@rm -rf $(OUT)/Payload $(OUT)/$(APP)-noext.ipa
	@rm -rf $(APP_DIR)/PlugIns
	@mkdir -p $(OUT)/Payload
	@cp -R $(APP_DIR) $(OUT)/Payload/
	@cd $(OUT) && zip -qry $(APP)-noext.ipa Payload
	@echo "built: $(OUT)/$(APP)-noext.ipa"