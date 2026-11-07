//
//  KeShareWidgetBundle.swift
//  KeShare Widget 扩展 —— 灵动岛 / 锁屏实时活动
//

import WidgetKit
import SwiftUI
import ActivityKit

@available(iOS 16.1, *)
struct KeShareActivityWidget: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: KeShareActivityAttributes.self) { context in
            // 锁屏 / 通知中心横幅
            LockScreenView(context: context)
                .activityBackgroundTint(Color.black.opacity(0.55))
                .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            let state = context.state
            let tint: Color = state.failed ? .red : (state.finished ? .green : .cyan)

            return DynamicIsland {
                // ---- 展开态 ----
                DynamicIslandExpandedRegion(.leading) {
                    Image(systemName: state.finished ? "checkmark.circle.fill"
                                     : (state.failed ? "xmark.circle.fill" : "arrow.up.arrow.down.circle.fill"))
                        .font(.title2)
                        .foregroundStyle(tint)
                        .padding(.leading, 6)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text("\(Int(state.progress * 100))%")
                        .font(.system(size: 15, weight: .semibold, design: .rounded))
                        .foregroundStyle(.white)
                        .padding(.trailing, 6)
                }
                DynamicIslandExpandedRegion(.center) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(context.attributes.title)
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(.white)
                            .lineLimit(1)
                        Text(state.detail)
                            .font(.system(size: 11))
                            .foregroundStyle(.white.opacity(0.7))
                            .lineLimit(1)
                    }
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(spacing: 6) {
                        ProgressView(value: state.progress)
                            .progressViewStyle(.linear)
                            .tint(tint)
                        HStack {
                            Text(ksFormatBytes(state.done))
                            Spacer()
                            Text(ksFormatBytes(state.total))
                        }
                        .font(.system(size: 10, design: .rounded))
                        .foregroundStyle(.white.opacity(0.6))
                    }
                    .padding(.horizontal, 4)
                    .padding(.bottom, 4)
                }
            } compactLeading: {
                // ---- 紧凑态（左） ----
                Image(systemName: state.finished ? "checkmark" : "arrow.down")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(tint)
            } compactTrailing: {
                // ---- 紧凑态（右）：进度环 ----
                ZStack {
                    Circle()
                        .stroke(Color.white.opacity(0.25), lineWidth: 2.5)
                    Circle()
                        .trim(from: 0, to: max(0.01, state.progress))
                        .stroke(tint, style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                }
                .frame(width: 18, height: 18)
            } minimal: {
                // ---- 极简态 ----
                ZStack {
                    Circle()
                        .stroke(Color.white.opacity(0.25), lineWidth: 2)
                    Circle()
                        .trim(from: 0, to: max(0.01, state.progress))
                        .stroke(tint, style: StrokeStyle(lineWidth: 2, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                }
                .frame(width: 16, height: 16)
            }
            .keylineTint(tint)
        }
    }
}

@available(iOS 16.1, *)
private struct LockScreenView: View {
    let context: ActivityViewContext<KeShareActivityAttributes>

    var body: some View {
        let state = context.state
        let tint: Color = state.failed ? .red : (state.finished ? .green : .cyan)
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: state.finished ? "checkmark.circle.fill"
                                 : (state.failed ? "xmark.circle.fill" : "arrow.up.arrow.down.circle.fill"))
                    .font(.title3)
                    .foregroundStyle(tint)
                VStack(alignment: .leading, spacing: 1) {
                    Text(context.attributes.title)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.white)
                    Text(state.detail)
                        .font(.system(size: 12))
                        .foregroundStyle(.white.opacity(0.68))
                }
                Spacer()
                Text("\(Int(state.progress * 100))%")
                    .font(.system(size: 15, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white)
            }
            ProgressView(value: state.progress)
                .progressViewStyle(.linear)
                .tint(tint)
            HStack {
                Text(ksFormatBytes(state.done))
                Spacer()
                Text(ksFormatBytes(state.total))
            }
            .font(.system(size: 11, design: .rounded))
            .foregroundStyle(.white.opacity(0.6))
        }
        .padding(14)
    }
}

@main
struct KeShareWidgetBundle: WidgetBundle {
    var body: some Widget {
        if #available(iOS 16.1, *) {
            KeShareActivityWidget()
        }
    }
}
