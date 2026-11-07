//
//  KSGlassKit.m -- iOS 自研玻璃视觉库（液态 + 磨砂）
//
//  与 Windows (glass.py) / Android (GlassPanel.java) 同一套设计：
//    * 液态：多段渐变 + 顶部镜面高光 + 内描边，通透
//    * 磨砂：低透明 + 噪点 + 更强底色，柔和
//    * 温控：NSProcessInfo.thermalState，fair 降质 / serious 及以上切磨砂
//    * 可选：默认液态 / 默认磨砂 / 强制液态，自动切换默认关闭
//
//  不自绘位图纹理，全部用 CoreGraphics 绘制；噪点用固定种子保证重绘一致。
//

#import "KeShare.h"
#import "KSInternal.h"

#pragma mark - 令牌

@implementation KSGlassTokens

+ (UIColor *)bg { return [UIColor colorWithRed:0.043 green:0.059 blue:0.102 alpha:1]; }
+ (UIColor *)bg2 { return [UIColor colorWithRed:0.055 green:0.078 blue:0.133 alpha:1]; }
+ (UIColor *)panel { return [UIColor colorWithRed:0.082 green:0.106 blue:0.169 alpha:1]; }
+ (UIColor *)stroke { return [UIColor colorWithRed:0.165 green:0.208 blue:0.314 alpha:1]; }
+ (UIColor *)fg { return [UIColor colorWithRed:0.949 green:0.961 blue:0.980 alpha:1]; }
+ (UIColor *)muted { return [UIColor colorWithRed:0.541 green:0.580 blue:0.675 alpha:1]; }

+ (UIColor *)mix:(UIColor *)a with:(UIColor *)b t:(CGFloat)t
{
    CGFloat r1 = 0, g1 = 0, b1 = 0, a1 = 0;
    CGFloat r2 = 0, g2 = 0, b2 = 0, a2 = 0;
    [a getRed:&r1 green:&g1 blue:&b1 alpha:&a1];
    [b getRed:&r2 green:&g2 blue:&b2 alpha:&a2];
    return [UIColor colorWithRed:r1 + (r2 - r1) * t
                           green:g1 + (g2 - g1) * t
                            blue:b1 + (b2 - b1) * t
                           alpha:a1 + (a2 - a1) * t];
}

@end

#pragma mark - 温控

@implementation KSGlassThermal

+ (instancetype)shared
{
    static KSGlassThermal *g;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ g = [[KSGlassThermal alloc] init]; });
    return g;
}

- (instancetype)init
{
    if ((self = [super init])) {
        _level = KSGlassQualityFull;
        [[NSNotificationCenter defaultCenter] addObserver:self
                                                 selector:@selector(thermalChanged:)
                                                     name:NSProcessInfoThermalStateDidChangeNotification
                                                   object:nil];
    }
    return self;
}

- (void)dealloc
{
    [[NSNotificationCenter defaultCenter] removeObserver:self];
}

- (void)thermalChanged:(NSNotification *)note { [self refresh]; }

- (void)refresh
{
    if (!self.enabled) {
        [self applyLevel:KSGlassQualityFull];
        return;
    }
    NSProcessInfoThermalState state = NSProcessInfo.processInfo.thermalState;
    switch (state) {
        case NSProcessInfoThermalStateNominal:
            [self applyLevel:KSGlassQualityFull];
            break;
        case NSProcessInfoThermalStateFair:
            [self applyLevel:KSGlassQualityReduced];
            break;
        case NSProcessInfoThermalStateSerious:
        case NSProcessInfoThermalStateCritical:
            [self applyLevel:KSGlassQualityFrosted];
            break;
    }
}

- (void)applyLevel:(KSGlassQuality)level
{
    if (level == _level) return;
    _level = level;
    [[NSNotificationCenter defaultCenter] postNotificationName:@"KSGlassQualityDidChange"
                                                        object:nil];
}

@end

#pragma mark - 玻璃视图

@interface KSGlassView2 ()
@property (nonatomic, strong) CAGradientLayer *gradient;
@property (nonatomic, strong) CAShapeLayer *strokeLayer;
@property (nonatomic, strong) CAShapeLayer *highlight;
@end

@implementation KSGlassView2

+ (Class)layerClass { return [CALayer class]; }

- (instancetype)initWithFrame:(CGRect)frame
{
    if ((self = [super initWithFrame:frame])) {
        self.backgroundColor = UIColor.clearColor;
        self.style = KSGlassKindLiquid;
        self.radius = 18;
        self.clipsToBounds = NO;
        [self buildLayers];
        [[NSNotificationCenter defaultCenter] addObserver:self
                                                 selector:@selector(qualityChanged:)
                                                     name:@"KSGlassQualityDidChange"
                                                   object:nil];
    }
    return self;
}

- (void)dealloc
{
    [[NSNotificationCenter defaultCenter] removeObserver:self];
}

- (void)qualityChanged:(NSNotification *)note { [self applyStyle]; }

- (void)buildLayers
{
    self.gradient = [CAGradientLayer layer];
    self.gradient.startPoint = CGPointMake(0.5, 0);
    self.gradient.endPoint = CGPointMake(0.5, 1);
    [self.layer insertSublayer:self.gradient atIndex:0];

    self.strokeLayer = [CAShapeLayer layer];
    self.strokeLayer.fillColor = UIColor.clearColor.CGColor;
    self.strokeLayer.lineWidth = 1;
    [self.layer insertSublayer:self.strokeLayer above:self.gradient];

    self.highlight = [CAShapeLayer layer];
    self.highlight.fillColor = UIColor.clearColor.CGColor;
    self.highlight.lineWidth = 1;
    [self.layer insertSublayer:self.highlight above:self.strokeLayer];

    [self applyStyle];
}

- (void)setStyle:(KSGlassKind)style
{
    _style = style;
    [self applyStyle];
}

- (void)setRadius:(CGFloat)radius
{
    _radius = radius;
    [self applyStyle];
}

- (void)layoutSubviews
{
    [super layoutSubviews];
    [self applyStyle];
}

- (void)applyStyle
{
    if (!self.gradient) return;
    CGRect bounds = self.bounds;
    if (CGRectIsEmpty(bounds)) return;

    KSGlassQuality quality = KSGlassThermal.shared.level;
    BOOL frosted = (self.style == KSGlassKindFrosted) ||
                   (quality == KSGlassQualityFrosted && self.style != KSGlassKindLiquidForced);

    UIColor *panel = KSGlassTokens.panel;
    UIColor *base = frosted
        ? [KSGlassTokens mix:panel with:KSGlassTokens.bg t:0.55]
        : [KSGlassTokens mix:panel with:KSGlassTokens.bg t:0.25];

    [CATransaction begin];
    [CATransaction setDisableActions:YES];

    self.gradient.frame = bounds;
    self.gradient.cornerRadius = self.radius;

    if (frosted) {
        CGFloat a = (quality == KSGlassQualityReduced) ? 0.94 : 0.90;
        self.gradient.colors = @[(id)[base colorWithAlphaComponent:a].CGColor,
                                 (id)[base colorWithAlphaComponent:a].CGColor];
        self.gradient.locations = @[@0, @1];
    } else {
        UIColor *top = [KSGlassTokens mix:base with:UIColor.whiteColor t:0.16];
        UIColor *bottom = [KSGlassTokens mix:base with:UIColor.blackColor t:0.22];
        self.gradient.colors = @[(id)[top colorWithAlphaComponent:0.92].CGColor,
                                 (id)[base colorWithAlphaComponent:0.88].CGColor,
                                 (id)[bottom colorWithAlphaComponent:0.92].CGColor];
        self.gradient.locations = @[@0, @0.55, @1];
    }

    UIBezierPath *path = [UIBezierPath bezierPathWithRoundedRect:bounds
                                                   cornerRadius:self.radius];
    self.strokeLayer.path = path.CGPath;
    self.strokeLayer.strokeColor = frosted
        ? KSGlassTokens.stroke.CGColor
        : [[KSGlassTokens mix:KSGlassTokens.stroke with:KSTheme.shared.accent t:0.25] CGColor];

    self.highlight.path = path.CGPath;
    self.highlight.strokeColor = [[UIColor whiteColor] colorWithAlphaComponent:
                                  (quality == KSGlassQualityReduced ? 0.06 : 0.14)].CGColor;

    [CATransaction commit];
}

@end
