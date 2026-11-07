//
//  KSApp.m -- App 生命周期、液态玻璃外观、标签栏、公共控件
//  KeShare 1.0
//

#import "KeShare.h"
#import "KSInternal.h"
#import <QuartzCore/QuartzCore.h>

#pragma mark - 主题色

@implementation KSTheme

+ (KSTheme *)shared
{
    static KSTheme *t;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        t = [[KSTheme alloc] init];
        t.accent = [UIColor colorWithRed:0.36 green:0.72 blue:1.00 alpha:1.0];
        t.accent2 = [UIColor colorWithRed:0.68 green:0.44 blue:1.00 alpha:1.0];
        t.textPrimary = [UIColor whiteColor];
        t.textSecondary = [UIColor colorWithWhite:1.0 alpha:0.62];
        t.cardStroke = [UIColor colorWithWhite:1.0 alpha:0.14];
    });
    return t;
}

@end

#pragma mark - 背景

@implementation KSBackgroundView
{
    CAGradientLayer *_grad;
    NSMutableArray<UIView *> *_blobs;
    CADisplayLink *_link;
    CFTimeInterval _t0;
}

- (instancetype)initWithFrame:(CGRect)frame
{
    if ((self = [super initWithFrame:frame])) {
        self.userInteractionEnabled = NO;
        _grad = [CAGradientLayer layer];
        _grad.colors = @[
            (id)[UIColor colorWithRed:0.05 green:0.06 blue:0.12 alpha:1.0].CGColor,
            (id)[UIColor colorWithRed:0.08 green:0.10 blue:0.20 alpha:1.0].CGColor,
            (id)[UIColor colorWithRed:0.04 green:0.05 blue:0.10 alpha:1.0].CGColor
        ];
        _grad.startPoint = CGPointMake(0, 0);
        _grad.endPoint = CGPointMake(1, 1);
        [self.layer addSublayer:_grad];

        _blobs = [NSMutableArray array];
        NSArray *colors = @[
            [UIColor colorWithRed:0.28 green:0.52 blue:1.0 alpha:0.34],
            [UIColor colorWithRed:0.60 green:0.30 blue:0.95 alpha:0.30],
            [UIColor colorWithRed:0.20 green:0.70 blue:0.80 alpha:0.22]
        ];
        for (int i = 0; i < 3; i++) {
            UIView *b = [[UIView alloc] init];
            b.backgroundColor = colors[i];
            b.layer.cornerRadius = 110;
            b.layer.filters = nil;
            b.layer.compositingFilter = @"screenBlendMode";
            b.layer.shadowColor = ((UIColor *)colors[i]).CGColor;
            b.layer.shadowRadius = 70;
            b.layer.shadowOpacity = 0.8;
            b.layer.shadowOffset = CGSizeZero;
            [self addSubview:b];
            [_blobs addObject:b];
        }
    }
    return self;
}

- (void)layoutSubviews
{
    [super layoutSubviews];
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    _grad.frame = self.bounds;
    [CATransaction commit];
    CGSize s = self.bounds.size;
    if (_blobs.count == 3) {
        _blobs[0].frame = CGRectMake(-60, -40, 220, 220);
        _blobs[1].frame = CGRectMake(s.width - 150, s.height * 0.12, 240, 240);
        _blobs[2].frame = CGRectMake(s.width * 0.2, s.height - 180, 200, 200);
    }
}

- (void)didMoveToWindow
{
    [super didMoveToWindow];
    if (self.window) {
        _t0 = CACurrentMediaTime();
        if (!_link) {
            _link = [CADisplayLink displayLinkWithTarget:self selector:@selector(tick)];
            [_link addToRunLoop:NSRunLoop.mainRunLoop forMode:NSRunLoopCommonModes];
        }
        UIAccessibilityIsReduceMotionEnabled() ? (_link.paused = YES) : (_link.paused = NO);
    } else {
        [_link invalidate];
        _link = nil;
    }
}

- (void)tick
{
    CFTimeInterval t = CACurrentMediaTime() - _t0;
    CGSize s = self.bounds.size;
    if (_blobs.count != 3) return;
    _blobs[0].center = CGPointMake(s.width * 0.28 + sin(t * 0.31) * 46,
                                   s.height * 0.16 + cos(t * 0.27) * 38);
    _blobs[1].center = CGPointMake(s.width * 0.82 + cos(t * 0.23) * 52,
                                   s.height * 0.34 + sin(t * 0.35) * 44);
    _blobs[2].center = CGPointMake(s.width * 0.45 + sin(t * 0.19) * 60,
                                   s.height * 0.86 + cos(t * 0.21) * 30);
}

@end

#pragma mark - 玻璃卡片

@implementation KSGlassView
{
    UIVisualEffectView *_blur;
    UIView *_tint;
    UIView *_stroke;
}

+ (instancetype)glassView
{
    return [[self alloc] initWithFrame:CGRectZero];
}

- (instancetype)initWithFrame:(CGRect)frame
{
    if ((self = [super initWithFrame:frame])) {
        _cornerRadius = 18;
        self.backgroundColor = [UIColor clearColor];
        self.layer.cornerRadius = _cornerRadius;
        self.layer.cornerCurve = kCACornerCurveContinuous;
        self.clipsToBounds = YES;

        // iOS 26+ 的液态玻璃
        Class glassClass = NSClassFromString(@"UIGlassEffect");
        if (glassClass && [glassClass respondsToSelector:@selector(effectWithStyle:)]) {
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Warc-performSelector-leaks"
            id effect = nil;
            SEL sel = NSSelectorFromString(@"effectWithStyle:");
            if ([glassClass respondsToSelector:sel]) {
                NSMethodSignature *sig = [glassClass methodSignatureForSelector:sel];
                NSInvocation *inv = [NSInvocation invocationWithMethodSignature:sig];
                [inv setTarget:glassClass];
                [inv setSelector:sel];
                NSInteger style = 0;
                [inv setArgument:&style atIndex:2];
                [inv invoke];
                [inv getReturnValue:&effect];
            }
#pragma clang diagnostic pop
            if (effect) {
                UIVisualEffectView *g = [[UIVisualEffectView alloc] initWithEffect:effect];
                g.frame = self.bounds;
                g.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
                [self addSubview:g];
                _blur = g;
            }
        }
        if (!_blur) {
            UIBlurEffect *e = [UIBlurEffect effectWithStyle:UIBlurEffectStyleSystemUltraThinMaterialDark];
            _blur = [[UIVisualEffectView alloc] initWithEffect:e];
            _blur.frame = self.bounds;
            _blur.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
            [self addSubview:_blur];
        }

        _tint = [[UIView alloc] initWithFrame:self.bounds];
        _tint.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        _tint.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.05];
        [self addSubview:_tint];

        _stroke = [[UIView alloc] initWithFrame:self.bounds];
        _stroke.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        _stroke.backgroundColor = [UIColor clearColor];
        _stroke.layer.borderColor = KSTheme.shared.cardStroke.CGColor;
        _stroke.layer.borderWidth = 1.0;
        _stroke.layer.cornerRadius = _cornerRadius;
        _stroke.layer.cornerCurve = kCACornerCurveContinuous;
        _stroke.userInteractionEnabled = NO;
        [self addSubview:_stroke];
    }
    return self;
}

- (void)setCornerRadius:(CGFloat)cornerRadius
{
    _cornerRadius = cornerRadius;
    self.layer.cornerRadius = cornerRadius;
    _stroke.layer.cornerRadius = cornerRadius;
}

- (void)layoutSubviews
{
    [super layoutSubviews];
    _blur.frame = self.bounds;
    _tint.frame = self.bounds;
    _stroke.frame = self.bounds;
}

@end

#pragma mark - 分组标题

@implementation KSSectionHeaderView

- (instancetype)initWithReuseIdentifier:(NSString *)reuseIdentifier
{
    if ((self = [super initWithReuseIdentifier:reuseIdentifier])) {
        _titleLabel = [[UILabel alloc] init];
        _titleLabel.font = [UIFont systemFontOfSize:13 weight:UIFontWeightSemibold];
        _titleLabel.textColor = KSTheme.shared.textSecondary;
        _titleLabel.translatesAutoresizingMaskIntoConstraints = NO;
        self.contentView.backgroundColor = [UIColor clearColor];
        [self.contentView addSubview:_titleLabel];
        [NSLayoutConstraint activateConstraints:@[
            [_titleLabel.leadingAnchor constraintEqualToAnchor:self.contentView.leadingAnchor constant:20],
            [_titleLabel.bottomAnchor constraintEqualToAnchor:self.contentView.bottomAnchor constant:-6]
        ]];
    }
    return self;
}

@end

#pragma mark - 基类控制器

@interface KSViewController ()
@end

@implementation KSViewController
{
    KSBackgroundView *_bg;
}

- (void)viewDidLoad
{
    [super viewDidLoad];
    _bg = [[KSBackgroundView alloc] initWithFrame:self.view.bounds];
    _bg.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    [self.view insertSubview:_bg atIndex:0];
    self.view.backgroundColor = [UIColor colorWithRed:0.04 green:0.05 blue:0.10 alpha:1.0];

    if (@available(iOS 13.0, *)) {
        UINavigationBarAppearance *ap = [[UINavigationBarAppearance alloc] init];
        [ap configureWithTransparentBackground];
        ap.titleTextAttributes = @{NSForegroundColorAttributeName: UIColor.whiteColor};
        ap.largeTitleTextAttributes = @{NSForegroundColorAttributeName: UIColor.whiteColor};
        self.navigationItem.standardAppearance = ap;
        self.navigationItem.scrollEdgeAppearance = ap;
        self.navigationItem.compactAppearance = ap;
    }
    self.navigationController.navigationBar.tintColor = KSTheme.shared.accent;

    if (@available(iOS 15.0, *)) {
        // 让玻璃卡片延伸到内容区
    }
}

- (UIStatusBarStyle)preferredStatusBarStyle { return UIStatusBarStyleLightContent; }

@end

#pragma mark - AppDelegate

@interface KSAppDelegate : UIResponder <UIApplicationDelegate>
@property (nonatomic, strong) UIWindow *window;
@end

@implementation KSAppDelegate

- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)launchOptions
{
    [[KSLog shared] info:@"KeShare %@ 启动，设备 %@ (%@)", KS_VERSION_STRING,
        KSConfig.shared.deviceName, KSConfig.shared.deviceId];

    self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
    self.window.overrideUserInterfaceStyle = UIUserInterfaceStyleDark;

    UITabBarController *tab = [[UITabBarController alloc] init];
    tab.tabBar.tintColor = KSTheme.shared.accent;
    if (@available(iOS 13.0, *)) {
        UITabBarAppearance *ap = [[UITabBarAppearance alloc] init];
        [ap configureWithTransparentBackground];
        ap.backgroundEffect = [UIBlurEffect effectWithStyle:UIBlurEffectStyleSystemUltraThinMaterialDark];
        tab.tabBar.standardAppearance = ap;
        if (@available(iOS 15.0, *)) tab.tabBar.scrollEdgeAppearance = ap;
    }

    NSArray *make = @[
        @[@"KSDevicesViewController", @"附近设备", @"antenna.radiowaves.left.and.right"],
        @[@"KSFilesViewController", @"文件", @"folder"],
        @[@"KSTransfersViewController", @"传输", @"arrow.up.arrow.down"],
        @[@"KSSettingsViewController", @"设置", @"gearshape"]
    ];
    NSMutableArray *vcs = [NSMutableArray array];
    for (NSArray *item in make) {
        Class cls = NSClassFromString(item[0]);
        UIViewController *vc = [[cls alloc] init];
        UINavigationController *nav = [[UINavigationController alloc] initWithRootViewController:vc];
        nav.navigationBar.prefersLargeTitles = YES;
        vc.navigationItem.title = item[1];
        vc.tabBarItem = [[UITabBarItem alloc] initWithTitle:item[1]
                                                     image:[UIImage systemImageNamed:item[2]]
                                                       tag:(NSInteger)[vcs count]];
        [vcs addObject:nav];
    }
    if (KSConfig.shared.developerUnlocked && NSClassFromString(@"KSDeveloperViewController")) {
        UIViewController *dev = [[NSClassFromString(@"KSDeveloperViewController") alloc] init];
        UINavigationController *nav = [[UINavigationController alloc] initWithRootViewController:dev];
        nav.navigationBar.prefersLargeTitles = YES;
        dev.navigationItem.title = @"开发者";
        dev.tabBarItem = [[UITabBarItem alloc] initWithTitle:@"开发者"
                                                      image:[UIImage systemImageNamed:@"hammer"]
                                                        tag:99];
        [vcs addObject:nav];
    }
    tab.viewControllers = vcs;
    self.window.rootViewController = tab;
    [self.window makeKeyAndVisible];

    [[KSServer shared] start];
    return YES;
}

- (void)applicationDidEnterBackground:(UIApplication *)application
{
    [[KSLog shared] info:@"进入后台"];
    [[KSServer shared] stop];
}

- (void)applicationWillEnterForeground:(UIApplication *)application
{
    [[KSLog shared] info:@"回到前台"];
    [[KSServer shared] start];
}

- (void)applicationWillTerminate:(UIApplication *)application
{
    [[KSServer shared] stop];
}

@end


