//
//  KeShareActivityAttributes.swift
//  灵动岛 / 实时活动 —— 传输进度
//
//  这个文件同时被主 App 和 Widget 扩展编译（两边必须完全一致）。
//

import Foundation
import ActivityKit

@available(iOS 16.1, *)
struct KeShareActivityAttributes: ActivityAttributes {

    public struct ContentState: Codable, Hashable {
        /// 0...1
        public var progress: Double
        /// 已传字节
        public var done: UInt64
        /// 总字节
        public var total: UInt64
        /// 一行说明：对端名称 + 状态
        public var detail: String
        /// 是否结束（结束后不再更新）
        public var finished: Bool
        /// 是否失败
        public var failed: Bool

        public init(progress: Double, done: UInt64, total: UInt64,
                    detail: String, finished: Bool = false, failed: Bool = false) {
            self.progress = progress
            self.done = done
            self.total = total
            self.detail = detail
            self.finished = finished
            self.failed = failed
        }
    }

    /// 标题，如 “接收 photo.jpg”
    public var title: String
    /// 副标题，如设备名
    public var subtitle: String

    public init(title: String, subtitle: String) {
        self.title = title
        self.subtitle = subtitle
    }
}

/// 字节数格式化（App 与扩展共用，避免依赖 UIKit）
func ksFormatBytes(_ v: UInt64) -> String {
    if v < 1024 { return "\(v) B" }
    let kb = Double(v) / 1024.0
    if kb < 1024 { return String(format: "%.1f KB", kb) }
    let mb = kb / 1024.0
    if mb < 1024 { return String(format: "%.1f MB", mb) }
    return String(format: "%.2f GB", mb / 1024.0)
}
