//
//  KSRoot.m -- 自适应根容器
//  KeShare 0.0.1 beta1
//
//  宽屏（iPad 横屏 / 大窗口）：左侧边栏 + 右侧内容区，与 Windows 端一致
//  窄屏（iPhone 竖屏）：退回系统标签栏（Apple 的常规做法）
//
//  侧边栏使用系统材质 UIGlassEffect（iOS 26+）/ UIBlurEffect 兜底，不自绘。
//

#import "KeShare.h"
#import "KSInternal.h"
#import <objc/message.h>

@interface KSRootController : UIViewController <UITableViewDataSource, UITableViewDelegate>
@property (nonatomic, strong) NSArray<UINavigationController *> *pages;
@property (nonatomic, strong) NSArray<NSString *> *titles;
@property (nonatomic, strong) NSArray<NSString *> *glyphs;
@property (nonatomic, strong) UITableView *sidebar;
@property (nonatomic, strong) UIView *content;
@property (nonatomic, assign) NSInteger currentIndex;
@end

@implementation KSRootController

- (instancetype)initWithPages:(NSArray<UINavigationController *> *)pages
                       titles:(NSArray<NSString *> *)titles
                       glyphs:(NSArray<NSString *> *)glyphs
{
    if ((self = [super init])) {
        _pages = pages;
        _titles = titles;
        _glyphs = glyphs;
        _currentIndex = 0;
    }
    return self;
}

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.view.backgroundColor = KSGlassTokens.bg;

    // ---- 侧边栏 ----
    self.sidebar = [[UITableView alloc] initWithFrame:CGRectZero style:UITableViewStylePlain];
    self.sidebar.backgroundColor = [UIColor clearColor];
    self.sidebar.separatorStyle = UITableViewCellSeparatorStyleNone;
    self.sidebar.dataSource = self;
    self.sidebar.delegate = self;
    self.sidebar.translatesAutoresizingMaskIntoConstraints = NO;
    self.sidebar.rowHeight = 52;
    [self.view addSubview:self.sidebar];

    // 侧边栏背后的系统材质
    UIVisualEffect *effect = nil;
    Class glass = NSClassFromString(@"UIGlassEffect");
    if (glass && [glass respondsToSelector:@selector(effectWithStyle:)]) {
        effect = ((UIVisualEffect * (*)(id, SEL, NSInteger))objc_msgSend)(
            glass, @selector(effectWithStyle:), 0);
    }
    if (![effect isKindOfClass:[UIVisualEffect class]]) {
        effect = [UIBlurEffect effectWithStyle:UIBlurEffectStyleSystemUltraThinMaterialDark];
    }
    UIVisualEffectView *backdrop = [[UIVisualEffectView alloc] initWithEffect:effect];
    backdrop.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view insertSubview:backdrop belowSubview:self.sidebar];

    // ---- 内容区 ----
    self.content = [[UIView alloc] initWithFrame:CGRectZero];
    self.content.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:self.content];

    [NSLayoutConstraint activateConstraints:@[
        [backdrop.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor],
        [backdrop.topAnchor constraintEqualToAnchor:self.view.topAnchor],
        [backdrop.bottomAnchor constraintEqualToAnchor:self.view.bottomAnchor],
        [backdrop.widthAnchor constraintEqualToConstant:232],

        [self.sidebar.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor],
        [self.sidebar.topAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor
                                                constant:56],
        [self.sidebar.bottomAnchor constraintEqualToAnchor:self.view.bottomAnchor],
        [self.sidebar.widthAnchor constraintEqualToConstant:232],

        [self.content.leadingAnchor constraintEqualToAnchor:self.sidebar.trailingAnchor],
        [self.content.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor],
        [self.content.topAnchor constraintEqualToAnchor:self.view.topAnchor],
        [self.content.bottomAnchor constraintEqualToAnchor:self.view.bottomAnchor],
    ]];

    [self showIndex:0];
}

- (void)showIndex:(NSInteger)index
{
    if (index < 0 || index >= (NSInteger)self.pages.count) return;
    for (UIViewController *child in self.childViewControllers) {
        [child.view removeFromSuperview];
        [child removeFromParentViewController];
    }
    UINavigationController *nav = self.pages[index];
    [self addChildViewController:nav];
    nav.view.frame = self.content.bounds;
    nav.view.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    [self.content addSubview:nav.view];
    [nav didMoveToParentViewController:self];
    self.currentIndex = index;
    [self.sidebar reloadData];
}

#pragma mark - 侧边栏数据

- (NSInteger)tableView:(UITableView *)tv numberOfRowsInSection:(NSInteger)section
{
    return self.pages.count;
}

- (UITableViewCell *)tableView:(UITableView *)tv cellForRowAtIndexPath:(NSIndexPath *)ip
{
    static NSString *cid = @"nav";
    UITableViewCell *cell = [tv dequeueReusableCellWithIdentifier:cid];
    if (!cell) {
        cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleDefault
                                      reuseIdentifier:cid];
        cell.backgroundColor = [UIColor clearColor];
        cell.selectionStyle = UITableViewCellSelectionStyleNone;
        cell.textLabel.font = [UIFont systemFontOfSize:14 weight:UIFontWeightSemibold];
    }
    BOOL active = (ip.row == self.currentIndex);
    cell.textLabel.text = [NSString stringWithFormat:@"%@   %@",
                           self.glyphs[ip.row], self.titles[ip.row]];
    cell.textLabel.textColor = active ? KSTheme.shared.accent
                                      : [UIColor colorWithWhite:1 alpha:0.62];

    // 选中态：圆角高亮条
    UIView *bar = [cell.contentView viewWithTag:9101];
    if (!bar) {
        bar = [[UIView alloc] initWithFrame:CGRectMake(8, 6, 216, 40)];
        bar.tag = 9101;
        bar.layer.cornerRadius = 12;
        bar.layer.cornerCurve = kCACornerCurveContinuous;
        bar.autoresizingMask = UIViewAutoresizingFlexibleWidth;
        [cell.contentView insertSubview:bar atIndex:0];
    }
    bar.hidden = !active;
    bar.backgroundColor = [KSTheme.shared.accent colorWithAlphaComponent:0.18];
    return cell;
}

- (void)tableView:(UITableView *)tv didSelectRowAtIndexPath:(NSIndexPath *)ip
{
    [tv deselectRowAtIndexPath:ip animated:NO];
    [self showIndex:ip.row];
}

@end

#pragma mark - 工厂

UIViewController *KSRootMakeIfWide(NSArray<UINavigationController *> *pages,
                                   NSArray<NSString *> *titles,
                                   NSArray<NSString *> *glyphs)
{
    BOOL wide = UIScreen.mainScreen.bounds.size.width >= 700;
    if (!wide) return nil;
    return [[KSRootController alloc] initWithPages:pages titles:titles glyphs:glyphs];
}
