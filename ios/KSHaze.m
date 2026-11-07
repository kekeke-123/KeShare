//
//  KSHaze.m -- Haze 视觉语言（iOS 实现）
//  KeShare 0.0.1 beta1
//
//  "Haze" 指磨砂玻璃：半透明 + 背景模糊 + 圆角 + 1px 描边 + 顶部柔光。
//  不含折射、不含色散（液态玻璃已从工程移除）。
//
//  iOS 上的磨砂由系统材质承担（UIVisualEffectView），本文件只负责：
//    * 统一令牌（颜色 / 圆角 / 不透明度 / 描边）
//    * 面板构建与样式应用
//    * 供全工程复用的工厂方法
//

#import "KeShare.h"
#import "KSInternal.h"

CGFloat const KSHazeRadiusPanel = 22.0;
CGFloat const KSHazeRadiusPill = 34.0;
CGFloat const KSHazeBorderAlpha = 0.16;
CGFloat const KSHazeHighlightAlpha = 0.20;

@implementation KSHaze

+ (UIColor *)tintColor
{
    return [UIColor colorWithRed:0.071 green:0.094 blue:0.165 alpha:1.0];
}

+ (UIColor *)panelColor
{
    return [UIColor colorWithRed:0.082 green:0.106 blue:0.169 alpha:1.0];
}

+ (UIBlurEffect *)blurEffect
{
    if (@available(iOS 13.0, *)) {
        return [UIBlurEffect effectWithStyle:UIBlurEffectStyleSystemUltraThinMaterialDark];
    }
    return [UIBlurEffect effectWithStyle:UIBlurEffectStyleDark];
}

+ (instancetype)panelWithFrame:(CGRect)frame
                        radius:(CGFloat)radius
{
    KSHaze *view = [[KSHaze alloc] initWithFrame:frame];
    view.radius = radius;
    return view;
}

+ (instancetype)pillWithFrame:(CGRect)frame
{
    return [self panelWithFrame:frame radius:CGRectGetHeight(frame) / 2.0];
}

- (instancetype)initWithFrame:(CGRect)frame
{
    if ((self = [super initWithFrame:frame])) {
        _radius = KSHazeRadiusPanel;
        self.backgroundColor = UIColor.clearColor;
        self.layer.cornerRadius = _radius;
        self.layer.cornerCurve = kCACornerCurveContinuous;
        self.clipsToBounds = YES;

        // 磨砂底：系统材质（唯一"模糊"来源）
        UIVisualEffectView *blur = [[UIVisualEffectView alloc]
            initWithEffect:[KSHaze blurEffect]];
        blur.frame = self.bounds;
        blur.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        blur.userInteractionEnabled = NO;
        [self addSubview:blur];
        _blurView = blur;

        // 底色叠加：决定"厚度"
        _tintView = [[UIView alloc] initWithFrame:self.bounds];
        _tintView.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        _tintView.backgroundColor = [[KSHaze tintColor] colorWithAlphaComponent:0.62];
        _tintView.userInteractionEnabled = NO;
        [self addSubview:_tintView];

        // 顶部柔光
        _highlightView = [[UIView alloc] initWithFrame:self.bounds];
        _highlightView.autoresizingMask = UIViewAutoresizingFlexibleWidth;
        _highlightView.userInteractionEnabled = NO;
        _highlightView.backgroundColor = UIColor.clearColor;
        [self addSubview:_highlightView];

        // 1px 描边
        self.layer.borderWidth = 1.0 / UIScreen.mainScreen.scale;
        self.layer.borderColor = [[UIColor whiteColor]
            colorWithAlphaComponent:KSHazeBorderAlpha].CGColor;

        _content = [[UIView alloc] initWithFrame:self.bounds];
        _content.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        [self addSubview:_content];
    }
    return self;
}

- (void)layoutSubviews
{
    [super layoutSubviews];
    self.layer.cornerRadius = self.radius;

    // 顶部柔光：占上方 42% 高度的线性渐变
    CGRect glow = CGRectMake(0, 0, CGRectGetWidth(self.bounds),
                             CGRectGetHeight(self.bounds) * 0.42);
    _highlightView.frame = glow;
    if (!_highlightLayer) {
        _highlightLayer = [CAGradientLayer layer];
        _highlightLayer.colors = @[
            (id)[[UIColor whiteColor] colorWithAlphaComponent:KSHazeHighlightAlpha * 0.16].CGColor,
            (id)[[UIColor whiteColor] colorWithAlphaComponent:0.0].CGColor,
        ];
        [_highlightView.layer addSublayer:_highlightLayer];
    }
    _highlightLayer.frame = _highlightView.bounds;
}

- (void)setRadius:(CGFloat)radius
{
    _radius = radius;
    self.layer.cornerRadius = radius;
    [self setNeedsLayout];
}

- (void)setAlphaWeight:(CGFloat)alphaWeight
{
    _alphaWeight = alphaWeight;
    _tintView.backgroundColor = [[KSHaze tintColor] colorWithAlphaComponent:alphaWeight];
}

- (void)setHazeBorderAlpha:(CGFloat)value
{
    self.layer.borderColor = [[UIColor whiteColor] colorWithAlphaComponent:value].CGColor;
}

@end