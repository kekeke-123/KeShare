//
//  KSLegal.m -- 法律性文稿（用户协议与隐私政策）
//
//  规则（按需求）：
//    1. 首次启动强制展示，未同意不得进入主界面
//    2. 「我已阅读并同意」初始禁用
//    3. 两个条件同时满足才启用：滑到最底部 + 自进入本页起满 3 秒
//    4. 计时从进入页面开始，与滚动无关
//    5. 设置里可「撤销同意」，撤销后重新走本页
//

#import "KeShare.h"
#import "KSInternal.h"

static NSString *const kKSLegalBody =
@"KeShare 用户协议与隐私政策\n\n"
"生效日期：2026 年 11 月 1 日\n\n"
"一、服务说明\n"
"KeShare 是一款点对点文件互传工具。所有文件在本机与对端设备之间直接传输，"
"不经过我们的服务器，我们不会接触、存储、分析您的任何文件内容。\n\n"
"二、本地数据\n"
"1. 配置文件（settings.json）保存在应用私有目录，删除应用即随之删除。\n"
"2. 接收的文件保存在「接收的数据」目录，仅您本人可访问。\n"
"3. 断点续传的临时数据保存在系统缓存目录，超出保留期限会自动清理。\n\n"
"三、权限使用\n"
"· 本地网络：用于发现同一网络内的其他设备，并在设备之间建立直连。\n"
"· 蓝牙：用于在无可用网络时完成设备发现与握手。\n"
"· 相册与文件：仅当您主动选择要向对端发送的文件时读取，不做任何后台扫描。\n\n"
"四、内容安全\n"
"1. 传输前与接收后会对文件做本地敏感内容检测，用于提示风险。\n"
"2. 检测基于本地规则，可能存在误判，最终以您的判断为准。\n"
"3. 若您坚持发送被判定为可疑的文件，本机可能被限制发送同类文件，"
"并强制开启敏感内容提示。该限制保存在本机，重装应用不会解除。\n\n"
"五、您的责任\n"
"您承诺不利用本软件传输违反法律法规的内容。因您传输的内容产生的法律责任，"
"由您自行承担。\n\n"
"六、免责声明\n"
"本软件按「现状」提供。因网络环境、设备故障、系统限制导致的中断或数据丢失，"
"我们不承担赔偿责任，但会尽力保证传输的完整性校验。\n\n"
"七、协议变更\n"
"协议更新后会在应用内提示，需重新阅读并同意后方可继续使用。\n\n"
"八、联系我们\n"
"如对本协议有疑问，可通过应用内「关于」页面提供的方式反馈。\n\n"
"（您需要滑动到此处才能勾选同意）";

@interface KSLegalViewController : UIViewController <UITextViewDelegate>
@property (nonatomic, copy) void (^onFinish)(BOOL accepted);
@end

@implementation KSLegalViewController
{
    UITextView *_textView;
    UIButton *_agreeButton;
    UILabel *_hintLabel;
    NSTimeInterval _enterTime;
    BOOL _scrolledToBottom;
    NSTimer *_ticker;
}

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.view.backgroundColor = [UIColor colorWithWhite:0.05 alpha:1.0];
    self.title = @"用户协议与隐私政策";

    CGRect bounds = self.view.bounds;
    CGFloat bottomBar = 110;

    _textView = [[UITextView alloc] initWithFrame:CGRectMake(16, 76, bounds.size.width - 32,
                                                             bounds.size.height - 76 - bottomBar)];
    _textView.autoresizingMask = UIViewAutoresizingFlexibleHeight | UIViewAutoresizingFlexibleWidth;
    _textView.backgroundColor = [UIColor colorWithWhite:1 alpha:0.06];
    _textView.layer.cornerRadius = 16;
    _textView.layer.cornerCurve = kCACornerCurveContinuous;
    _textView.textColor = [UIColor colorWithWhite:1 alpha:0.9];
    _textView.font = [UIFont systemFontOfSize:13];
    _textView.editable = NO;
    _textView.delegate = self;
    _textView.text = kKSLegalBody;
    [self.view addSubview:_textView];

    _hintLabel = [[UILabel alloc] initWithFrame:CGRectMake(16, bounds.size.height - bottomBar + 6,
                                                           bounds.size.width - 32, 20)];
    _hintLabel.autoresizingMask = UIViewAutoresizingFlexibleTopMargin | UIViewAutoresizingFlexibleWidth;
    _hintLabel.textColor = [UIColor colorWithWhite:1 alpha:0.55];
    _hintLabel.font = [UIFont systemFontOfSize:12];
    _hintLabel.textAlignment = NSTextAlignmentCenter;
    [self.view addSubview:_hintLabel];

    _agreeButton = [UIButton buttonWithType:UIButtonTypeSystem];
    _agreeButton.frame = CGRectMake(16, bounds.size.height - bottomBar + 34,
                                    bounds.size.width - 32, 48);
    _agreeButton.autoresizingMask = UIViewAutoresizingFlexibleTopMargin | UIViewAutoresizingFlexibleWidth;
    [_agreeButton setTitle:@"我已阅读并同意" forState:UIControlStateNormal];
    _agreeButton.titleLabel.font = [UIFont systemFontOfSize:16 weight:UIFontWeightSemibold];
    _agreeButton.layer.cornerRadius = 24;
    _agreeButton.layer.cornerCurve = kCACornerCurveContinuous;
    [_agreeButton addTarget:self action:@selector(agreeTapped) forControlEvents:UIControlEventTouchUpInside];
    [self.view addSubview:_agreeButton];

    [self refreshGate];
}

- (void)viewDidAppear:(BOOL)animated
{
    [super viewDidAppear:animated];
    _enterTime = [NSDate date].timeIntervalSince1970;   // 计时从进入本页开始
    _ticker = [NSTimer scheduledTimerWithTimeInterval:0.2 target:self
                                             selector:@selector(refreshGate)
                                             userInfo:nil repeats:YES];
}

- (void)viewWillDisappear:(BOOL)animated
{
    [super viewWillDisappear:animated];
    [_ticker invalidate];
    _ticker = nil;
}

- (void)scrollViewDidScroll:(UIScrollView *)scrollView
{
    CGFloat offset = scrollView.contentOffset.y + scrollView.bounds.size.height;
    CGFloat height = scrollView.contentSize.height;
    BOOL atBottom = offset >= height - 12;
    if (atBottom && !_scrolledToBottom) {
        _scrolledToBottom = YES;
        [self refreshGate];
    } else if (!atBottom && _scrolledToBottom) {
        _scrolledToBottom = NO;
        [self refreshGate];
    }
}

- (void)refreshGate
{
    NSTimeInterval elapsed = _enterTime > 0
        ? [NSDate date].timeIntervalSince1970 - _enterTime : 0;
    BOOL timeOK = elapsed >= 3.0;
    BOOL ready = _scrolledToBottom && timeOK;

    _agreeButton.enabled = ready;
    _agreeButton.backgroundColor = ready
        ? KSTheme.shared.accent
        : [UIColor colorWithWhite:1 alpha:0.10];
    [_agreeButton setTitleColor:ready ? UIColor.whiteColor
                                    : [UIColor colorWithWhite:1 alpha:0.35]
                       forState:UIControlStateNormal];

    if (ready) {
        _hintLabel.text = @"已可确认";
    } else if (!_scrolledToBottom && !timeOK) {
        _hintLabel.text = [NSString stringWithFormat:@"请滑动到底部（还需 %.1f 秒）",
                           MAX(0.0, 3.0 - elapsed)];
    } else if (!_scrolledToBottom) {
        _hintLabel.text = @"请滑动到最底部";
    } else {
        _hintLabel.text = [NSString stringWithFormat:@"还需 %.1f 秒", MAX(0.0, 3.0 - elapsed)];
    }
}

- (void)agreeTapped
{
    [KSConfig.shared setLegalAccepted:YES];
    [[KSLog shared] info:@"用户已同意协议与隐私政策"];
    if (self.onFinish) self.onFinish(YES);
}

@end

#pragma mark - 对外入口

UIViewController *KSLegalMakeViewController(void (^onFinish)(BOOL accepted))
{
    KSLegalViewController *vc = [[KSLegalViewController alloc] init];
    vc.onFinish = onFinish;
    return vc;
}

void KSLegalPresentIfNeeded(UIViewController *host, void (^onAccepted)(void))
{
    if (KSConfig.shared.legalAccepted) return;
    UIViewController *legal = KSLegalMakeViewController(^(BOOL accepted) {
        if (!accepted) return;
        [host dismissViewControllerAnimated:YES completion:^{
            if (onAccepted) onAccepted();
        }];
    });
    legal.modalPresentationStyle = UIModalPresentationFullScreen;
    [host presentViewController:legal animated:YES completion:nil];
}