//
//  KSActivityManager.swift
//  实时活动管理（灵动岛）—— 以 @objc 导出给 Objective-C 调用
//
//  ObjC 侧用法：
//      Class cls = NSClassFromString(@"KSActivityManager");
//      id mgr = [cls performSelector:@selector(shared)];
//      [mgr startWithTitle:subtitle:total:]
//      [mgr updateWithProgress:detail:]
//      [mgr finishWithSuccess:message:]
//

import Foundation
import ActivityKit

@objc(KSActivityManager)
public final class KSActivityManager: NSObject {

    @objc(shared)
    public static let shared = KSActivityManager()

    private var activity: Any?          // Activity<KeShareActivityAttributes>?
    private var startTime: Date = Date()

    @objc(supported)
    public var supported: Bool {
        if #available(iOS 16.1, *) {
            return ActivityAuthorizationInfo().areActivitiesEnabled
        }
        return false
    }

    @objc(startWithTitle:subtitle:total:)
    public func start(title: String, subtitle: String, total: UInt64) {
        guard #available(iOS 16.1, *) else { return }
        endAll()
        startTime = Date()
        let attrs = KeShareActivityAttributes(title: title, subtitle: subtitle)
        let state = KeShareActivityAttributes.ContentState(
            progress: 0, done: 0, total: total,
            detail: "\(subtitle) · 准备中")
        do {
            let a = try Activity<KeShareActivityAttributes>.request(
                attributes: attrs,
                contentState: state,
                pushType: nil)
            activity = a
        } catch {
            NSLog("[KeShare] 实时活动启动失败: \(error.localizedDescription)")
        }
    }

    @objc(updateWithProgress:detail:)
    public func update(progress: Double, detail: String) {
        guard #available(iOS 16.1, *), let a = activity as? Activity<KeShareActivityAttributes> else { return }
        let total = currentTotal
        let state = KeShareActivityAttributes.ContentState(
            progress: max(0, min(1, progress)),
            done: UInt64(Double(total) * max(0, min(1, progress))),
            total: total,
            detail: detail)
        Task { await a.update(using: state) }
    }

    @objc(finishWithSuccess:message:)
    public func finish(success: Bool, message: String) {
        guard #available(iOS 16.1, *), let a = activity as? Activity<KeShareActivityAttributes> else { return }
        let total = currentTotal
        let state = KeShareActivityAttributes.ContentState(
            progress: success ? 1.0 : 0.0,
            done: success ? total : UInt64(0),
            total: total,
            detail: message,
            finished: true,
            failed: !success)
        Task {
            await a.end(using: state, dismissalPolicy: .after(Date().addingTimeInterval(success ? 6 : 12)))
        }
        activity = nil
    }

    @objc public func endAll() {
        guard #available(iOS 16.1, *) else { return }
        for a in Activity<KeShareActivityAttributes>.activities {
            Task { await a.end(dismissalPolicy: .immediate) }
        }
        activity = nil
    }

    private var currentTotal: UInt64 {
        guard #available(iOS 16.1, *), let a = activity as? Activity<KeShareActivityAttributes> else { return 0 }
        return a.contentState.total
    }
}
