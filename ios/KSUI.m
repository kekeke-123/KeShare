//
//  KSUI.m -- 四个主页面 + 开发者页
//  KeShare 1.0
//

#import "KeShare.h"
#import "KSInternal.h"

#pragma mark - 设备列表

@interface KSDevicesViewController () <UITableViewDataSource, UITableViewDelegate, KSSessionDelegate>
@property (nonatomic, strong) UITableView *table;
@property (nonatomic, strong) UILabel *statusLabel;
@property (nonatomic, strong) KSSession *session;
@end

@implementation KSDevicesViewController

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.table = [[UITableView alloc] initWithFrame:self.view.bounds style:UITableViewStyleInsetGrouped];
    self.table.backgroundColor = UIColor.clearColor;
    self.table.dataSource = self;
    self.table.delegate = self;
    self.table.rowHeight = 68;
    self.table.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    [self.view addSubview:self.table];

    self.navigationItem.rightBarButtonItems = @[
        [[UIBarButtonItem alloc] initWithImage:[UIImage systemImageNamed:@"arrow.clockwise"]
                                         style:UIBarButtonItemStylePlain
                                        target:self action:@selector(refresh)],
        [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemAdd
                                                      target:self action:@selector(manualConnect)]
    ];
    self.navigationItem.leftBarButtonItem =
        [[UIBarButtonItem alloc] initWithImage:[UIImage systemImageNamed:@"square.and.arrow.up"]
                                         style:UIBarButtonItemStylePlain
                                        target:self action:@selector(shareAppInfo)];

    [self buildOfflineBanner];

    [[NSNotificationCenter defaultCenter] addObserver:self.table selector:@selector(reloadData)
                                                 name:KSPeersDidChangeNotification object:nil];
    [self refresh];
}

- (void)manualConnect
{
    UIAlertController *al = [UIAlertController alertControllerWithTitle:@"手动连接"
                                                                message:@"输入对方 IP（Windows 端可在设置里看到本机地址）"
                                                         preferredStyle:UIAlertControllerStyleAlert];
    [al addTextFieldWithConfigurationHandler:^(UITextField *tf) {
        tf.placeholder = @"192.168.1.100";
        tf.keyboardType = UIKeyboardTypeDecimalPad;
        tf.text = @"192.168.1.100";
    }];
    [al addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
    [al addAction:[UIAlertAction actionWithTitle:@"连接" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) {
        NSString *ip = [al.textFields.firstObject.text stringByTrimmingCharactersInSet:
                        [NSCharacterSet whitespaceCharacterSet]];
        if (ip.length == 0) return;
        KSDevice *d = [[KSDevice alloc] init];
        d.deviceId = [NSString stringWithFormat:@"manual-%@", ip];
        d.name = ip;
        d.host = ip;
        d.port = (uint16_t)KSConfig.shared.port;
        d.kind = KSDeviceKindWindows;
        [[KSServer shared] addPeer:d];
        self.session = [[KSSession alloc] initWithPeer:d];
        self.session.delegate = self;
        [self.session startAsInitiator:YES];
        [self showStatus:[NSString stringWithFormat:@"正在连接 %@…", ip]];
    }]];
    [self presentViewController:al animated:YES completion:nil];
}
- (void)buildOfflineBanner
{
    KSGlassView *banner = [KSGlassView glassView];
    banner.translatesAutoresizingMaskIntoConstraints = NO;
    banner.cornerRadius = 16;
    banner.tag = 9001;
    [self.view addSubview:banner];

    UILabel *title = [[UILabel alloc] init];
    title.text = @"没有可用网络";
    title.font = [UIFont systemFontOfSize:14 weight:UIFontWeightSemibold];
    title.textColor = UIColor.whiteColor;
    title.translatesAutoresizingMaskIntoConstraints = NO;

    UILabel *desc = [[UILabel alloc] init];
    desc.text = [NSString stringWithFormat:@"让电脑开热点「%@」，再连上它即可互传",
                 KSConfig.shared.hotspotSSID];
    desc.font = [UIFont systemFontOfSize:11];
    desc.textColor = [UIColor colorWithWhite:1 alpha:0.66];
    desc.numberOfLines = 2;
    desc.translatesAutoresizingMaskIntoConstraints = NO;

    UIButton *settings = [UIButton buttonWithType:UIButtonTypeSystem];
    [settings setTitle:@"一键连接热点" forState:UIControlStateNormal];
    settings.titleLabel.font = [UIFont systemFontOfSize:12 weight:UIFontWeightSemibold];
    [settings addTarget:self action:@selector(joinKeShareHotspot) forControlEvents:UIControlEventTouchUpInside];
    settings.translatesAutoresizingMaskIntoConstraints = NO;

    UIButton *copy = [UIButton buttonWithType:UIButtonTypeSystem];
    [copy setTitle:@"复制热点信息" forState:UIControlStateNormal];
    copy.titleLabel.font = [UIFont systemFontOfSize:12];
    [copy addTarget:self action:@selector(copyHotspotInfo) forControlEvents:UIControlEventTouchUpInside];
    copy.translatesAutoresizingMaskIntoConstraints = NO;

    [banner addSubview:title];
    [banner addSubview:desc];
    [banner addSubview:settings];
    [banner addSubview:copy];

    [NSLayoutConstraint activateConstraints:@[
        [banner.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor constant:16],
        [banner.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor constant:-16],
        [banner.topAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor constant:6],
        [title.leadingAnchor constraintEqualToAnchor:banner.leadingAnchor constant:14],
        [title.topAnchor constraintEqualToAnchor:banner.topAnchor constant:12],
        [desc.leadingAnchor constraintEqualToAnchor:title.leadingAnchor],
        [desc.topAnchor constraintEqualToAnchor:title.bottomAnchor constant:2],
        [desc.trailingAnchor constraintLessThanOrEqualToAnchor:banner.trailingAnchor constant:-14],
        [settings.leadingAnchor constraintEqualToAnchor:title.leadingAnchor],
        [settings.topAnchor constraintEqualToAnchor:desc.bottomAnchor constant:6],
        [settings.bottomAnchor constraintEqualToAnchor:banner.bottomAnchor constant:-10],
        [copy.leadingAnchor constraintEqualToAnchor:settings.trailingAnchor constant:14],
        [copy.centerYAnchor constraintEqualToAnchor:settings.centerYAnchor]
    ]];
    banner.hidden = YES;
}

- (void)joinKeShareHotspot
{
    NSString *ssid = KSConfig.shared.hotspotSSID;
    NSString *pwd = KSConfig.shared.hotspotPassword;
    if (![KSWiFi hotspotJoinAvailable]) {
        [self openNetworkSettings];
        return;
    }
    [self showStatus:[NSString stringWithFormat:@"正在加入热点 %@ …", ssid]];
    [KSWiFi joinHotspot:ssid password:pwd completion:^(BOOL ok, NSString *message) {
        [self showStatus:ok ? @"已连接热点，正在搜索电脑…"
                            : [NSString stringWithFormat:@"加入失败：%@", message]];
        if (ok) {
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.2 * NSEC_PER_SEC)),
                           dispatch_get_main_queue(), ^{
                [[KSServer shared] autoConnectKnownPeers];
            });
        }
    }];
}

- (void)updateOfflineBanner
{
    UIView *banner = [self.view viewWithTag:9001];
    if (!banner) return;
    NSString *ip = KSIPv4StringLocal();
    BOOL offline = [ip isEqualToString:@"127.0.0.1"] || [ip hasPrefix:@"169.254."];
    banner.hidden = !offline;
    CGFloat top = offline ? 118.0 : 0.0;
    self.table.frame = CGRectMake(0, top, self.view.bounds.size.width,
                                  self.view.bounds.size.height - top);
}

- (void)openNetworkSettings
{
    NSArray *urls = @[@"App-Prefs:root=INTERNET_TETHERING",
                      @"App-Prefs:root=WIFI",
                      UIApplicationOpenSettingsURLString];
    for (NSString *s in urls) {
        NSURL *url = [NSURL URLWithString:s];
        if (url && [[UIApplication sharedApplication] canOpenURL:url]) {
            [[UIApplication sharedApplication] openURL:url options:@{} completionHandler:nil];
            return;
        }
    }
}

- (void)copyHotspotInfo
{
    NSString *text = [NSString stringWithFormat:@"Wi-Fi 名称：%@\n密码：%@",
                      KSConfig.shared.hotspotSSID, KSConfig.shared.hotspotPassword];
    UIPasteboard.generalPasteboard.string = text;
    [self showStatus:@"热点信息已复制到剪贴板"];
}

- (void)viewWillAppear:(BOOL)animated
{
    [super viewWillAppear:animated];
    [self updateOfflineBanner];
}
- (void)refresh
{
    [[KSServer shared] refreshBonjour];
    [self.table reloadData];
}

- (void)shareAppInfo
{
    NSString *text = [NSString stringWithFormat:@"KeShare %@  设备 ID: %@  地址: %@:%ld",
                      KS_VERSION_STRING, KSConfig.shared.deviceId,
                      @"(本机)", (long)KSConfig.shared.port];
    UIActivityViewController *av = [[UIActivityViewController alloc] initWithActivityItems:@[text] applicationActivities:nil];
    [self presentViewController:av animated:YES completion:nil];
}

- (NSInteger)tableView:(UITableView *)tv numberOfRowsInSection:(NSInteger)section
{
    return MAX(1, (NSInteger)[KSServer shared].peers.count);
}

- (UITableViewCell *)tableView:(UITableView *)tv cellForRowAtIndexPath:(NSIndexPath *)ip
{
    static NSString *cid = @"peer";
    UITableViewCell *cell = [tv dequeueReusableCellWithIdentifier:cid];
    if (!cell) {
        cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleSubtitle reuseIdentifier:cid];
        cell.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.06];
        cell.textLabel.textColor = UIColor.whiteColor;
        cell.detailTextLabel.textColor = KSThemeTextSecondary();
        cell.tintColor = UIColor.whiteColor;
        cell.accessoryType = UITableViewCellAccessoryDisclosureIndicator;
    }
    NSArray<KSDevice *> *peers = [KSServer shared].peers;
    if (peers.count == 0) {
        cell.textLabel.text = @"正在搜索附近设备…";
        cell.detailTextLabel.text = @"请确保对端已打开 KeShare 且在同一网络";
        cell.imageView.image = [UIImage systemImageNamed:@"antenna.radiowaves.left.and.right"];
        cell.accessoryType = UITableViewCellAccessoryNone;
        cell.selectionStyle = UITableViewCellSelectionStyleNone;
        return cell;
    }
    KSDevice *d = peers[ip.row];
    cell.textLabel.text = d.name;
    cell.detailTextLabel.text = [NSString stringWithFormat:@"%@ · %@ · %@:%u", d.kindLabel,
                                 d.osVersion.length ? d.osVersion : @"—", d.host ?: @"—", d.port];
    cell.imageView.image = [UIImage systemImageNamed:
        d.kind == KSDeviceKindWindows ? @"desktopcomputer" :
        d.kind == KSDeviceKindIOS ? @"iphone" : @"questionmark.circle"];
    cell.accessoryType = d.connected ? UITableViewCellAccessoryCheckmark : UITableViewCellAccessoryDisclosureIndicator;
    return cell;
}

- (void)tableView:(UITableView *)tv didSelectRowAtIndexPath:(NSIndexPath *)ip
{
    [tv deselectRowAtIndexPath:ip animated:YES];
    NSArray<KSDevice *> *peers = [KSServer shared].peers;
    if (ip.row >= (NSInteger)peers.count) return;
    KSDevice *d = peers[ip.row];
    self.session = [[KSSession alloc] initWithPeer:d];
    self.session.delegate = self;
    [self.session startAsInitiator:YES];
    [[KSLog shared] info:@"正在连接 %@", d.name];
    [self showStatus:[NSString stringWithFormat:@"正在连接 %@…", d.name]];
}

- (void)showStatus:(NSString *)text
{
    if (!self.statusLabel) {
        self.statusLabel = [[UILabel alloc] initWithFrame:CGRectMake(20, self.view.bounds.size.height - 90,
                                                                    self.view.bounds.size.width - 40, 36)];
        self.statusLabel.textColor = KSThemeTextSecondary();
        self.statusLabel.font = [UIFont systemFontOfSize:13];
        self.statusLabel.textAlignment = NSTextAlignmentCenter;
        self.statusLabel.autoresizingMask = UIViewAutoresizingFlexibleTopMargin;
        [self.view addSubview:self.statusLabel];
    }
    self.statusLabel.text = text;
}

- (void)session:(KSSession *)s didUpdatePeer:(KSDevice *)peer
{
    [self showStatus:[NSString stringWithFormat:@"%@ · %@", peer.name,
                      peer.connected ? @"已连接" : @"未连接"]];
    [self.table reloadData];
}

- (void)session:(KSSession *)s didReceiveTransferUpdate:(KSTransfer *)t {}
- (void)session:(KSSession *)s didFail:(NSString *)error { [self showStatus:error]; }

@end

#pragma mark - 文件

@interface KSFilesViewController () <UITableViewDataSource, UITableViewDelegate>
@property (nonatomic, strong) UITableView *table;
@property (nonatomic, strong) UISegmentedControl *segment;
@property (nonatomic, strong) NSArray<NSString *> *files;
@end

@implementation KSFilesViewController

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.segment = [[UISegmentedControl alloc] initWithItems:@[@"共享目录", @"已接收"]];
    self.segment.selectedSegmentIndex = 0;
    self.segment.translatesAutoresizingMaskIntoConstraints = NO;
    [self.segment addTarget:self action:@selector(reload) forControlEvents:UIControlEventValueChanged];
    [self.view addSubview:self.segment];

    self.table = [[UITableView alloc] initWithFrame:self.view.bounds style:UITableViewStyleInsetGrouped];
    self.table.backgroundColor = UIColor.clearColor;
    self.table.dataSource = self;
    self.table.delegate = self;
    self.table.rowHeight = 62;
    [self.view addSubview:self.table];

    [NSLayoutConstraint activateConstraints:@[
        [self.segment.topAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor constant:8],
        [self.segment.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor constant:20],
        [self.segment.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor constant:-20],
    ]];
    self.table.translatesAutoresizingMaskIntoConstraints = NO;
    [NSLayoutConstraint activateConstraints:@[
        [self.table.topAnchor constraintEqualToAnchor:self.segment.bottomAnchor constant:8],
        [self.table.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor],
        [self.table.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor],
        [self.table.bottomAnchor constraintEqualToAnchor:self.view.bottomAnchor],
    ]];

    self.navigationItem.rightBarButtonItem =
        [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemRefresh
                                                      target:self action:@selector(reload)];
    [self reload];
}

- (NSString *)currentDir
{
    return self.segment.selectedSegmentIndex == 0 ? KSConfig.shared.shareRoot : KSConfig.shared.receivePath;
}

- (void)reload
{
    NSString *dir = [self currentDir];
    NSArray *items = [[NSFileManager defaultManager] contentsOfDirectoryAtPath:dir error:NULL];
    self.files = [items sortedArrayUsingSelector:@selector(localizedStandardCompare:)];
    [self.table reloadData];
}

- (NSInteger)tableView:(UITableView *)tv numberOfRowsInSection:(NSInteger)section
{
    return MAX(1, (NSInteger)self.files.count);
}

- (UITableViewCell *)tableView:(UITableView *)tv cellForRowAtIndexPath:(NSIndexPath *)ip
{
    static NSString *cid = @"file";
    UITableViewCell *cell = [tv dequeueReusableCellWithIdentifier:cid];
    if (!cell) {
        cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleSubtitle reuseIdentifier:cid];
        cell.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.06];
        cell.textLabel.textColor = UIColor.whiteColor;
        cell.detailTextLabel.textColor = KSThemeTextSecondary();
        cell.accessoryType = UITableViewCellAccessoryDetailButton;
    }
    if (self.files.count == 0) {
        cell.textLabel.text = @"这里还没有文件";
        cell.detailTextLabel.text = @"收到的文件会出现在“已接收”";
        cell.imageView.image = [UIImage systemImageNamed:@"tray"];
        return cell;
    }
    NSString *name = self.files[ip.row];
    NSString *path = [[self currentDir] stringByAppendingPathComponent:name];
    cell.textLabel.text = name;
    cell.detailTextLabel.text = KSFormatBytes(KSFileSize(path));
    cell.imageView.image = [UIImage systemImageNamed:@"doc"];
    return cell;
}

- (void)tableView:(UITableView *)tv didSelectRowAtIndexPath:(NSIndexPath *)ip
{
    [tv deselectRowAtIndexPath:ip animated:YES];
    if (ip.row >= (NSInteger)self.files.count) return;
    NSString *name = self.files[ip.row];
    NSString *path = [[self currentDir] stringByAppendingPathComponent:name];

    UIAlertController *sheet = [UIAlertController alertControllerWithTitle:name
                                                                  message:KSFormatBytes(KSFileSize(path))
                                                           preferredStyle:UIAlertControllerStyleActionSheet];
    [sheet addAction:[UIAlertAction actionWithTitle:@"分享" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) {
        UIActivityViewController *av = [[UIActivityViewController alloc]
            initWithActivityItems:@[[NSURL fileURLWithPath:path]] applicationActivities:nil];
        [self presentViewController:av animated:YES completion:nil];
    }]];
    [sheet addAction:[UIAlertAction actionWithTitle:@"删除" style:UIAlertActionStyleDestructive handler:^(UIAlertAction *a) {
        [[NSFileManager defaultManager] removeItemAtPath:path error:NULL];
        [self reload];
    }]];
    [sheet addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
    sheet.popoverPresentationController.sourceView = tv;
    [self presentViewController:sheet animated:YES completion:nil];
}

@end

#pragma mark - 传输列表

@interface KSTransfersViewController () <UITableViewDataSource>
@property (nonatomic, strong) UITableView *table;
@property (nonatomic, strong) NSMutableArray<KSTransfer *> *items;
@end

@implementation KSTransfersViewController

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.items = [NSMutableArray array];
    self.table = [[UITableView alloc] initWithFrame:self.view.bounds style:UITableViewStyleInsetGrouped];
    self.table.backgroundColor = UIColor.clearColor;
    self.table.dataSource = self;
    self.table.rowHeight = 74;
    self.table.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    [self.view addSubview:self.table];

    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(update:)
                                                 name:KSTransfersDidChangeNotification object:nil];
}

- (void)update:(NSNotification *)note
{
    KSTransfer *t = note.object;
    if ([t isKindOfClass:[KSTransfer class]] && ![self.items containsObject:t]) {
        [self.items insertObject:t atIndex:0];
    }
    [self.table reloadData];
}

- (NSInteger)tableView:(UITableView *)tv numberOfRowsInSection:(NSInteger)section
{
    return MAX(1, (NSInteger)self.items.count);
}

- (UITableViewCell *)tableView:(UITableView *)tv cellForRowAtIndexPath:(NSIndexPath *)ip
{
    static NSString *cid = @"tr";
    UITableViewCell *cell = [tv dequeueReusableCellWithIdentifier:cid];
    if (!cell) {
        cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleSubtitle reuseIdentifier:cid];
        cell.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.06];
        cell.textLabel.textColor = UIColor.whiteColor;
        cell.detailTextLabel.textColor = KSThemeTextSecondary();
        cell.selectionStyle = UITableViewCellSelectionStyleNone;
        UIProgressView *pv = [[UIProgressView alloc] initWithProgressViewStyle:UIProgressViewStyleDefault];
        pv.tag = 99;
        pv.frame = CGRectMake(18, 56, tv.bounds.size.width - 90, 4);
        pv.progressTintColor = KSSystemAccent();
        [cell.contentView addSubview:pv];
    }
    if (self.items.count == 0) {
        cell.textLabel.text = @"还没有传输";
        cell.detailTextLabel.text = @"从“附近设备”连接后即可收发";
        cell.imageView.image = [UIImage systemImageNamed:@"arrow.up.arrow.down"];
        ((UIProgressView *)[cell.contentView viewWithTag:99]).hidden = YES;
        return cell;
    }
    KSTransfer *t = self.items[ip.row];
    ((UIProgressView *)[cell.contentView viewWithTag:99]).hidden = NO;
    cell.textLabel.text = t.fileName;
    cell.detailTextLabel.text = [NSString stringWithFormat:@"%@ · %@ · %@ / %@ · %@",
                                 t.peerName,
                                 t.direction == KSTransferDirectionUpload ? @"上传" : @"下载",
                                 KSFormatBytes(t.done), KSFormatBytes(t.total), t.stateLabel];
    cell.imageView.image = [UIImage systemImageNamed:
        t.direction == KSTransferDirectionUpload ? @"arrow.up.circle" : @"arrow.down.circle"];
    [(UIProgressView *)[cell.contentView viewWithTag:99] setProgress:(float)t.progress animated:YES];
    return cell;
}

@end

#pragma mark - 设置

@interface KSSettingsViewController () <UITableViewDataSource, UITableViewDelegate>
@property (nonatomic, strong) UITableView *table;
@property (nonatomic, strong) UILabel *versionLabel;
@property (nonatomic, assign) NSInteger tapCount;
@property (nonatomic, strong) NSTimer *tapTimer;
@end

@implementation KSSettingsViewController

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.table = [[UITableView alloc] initWithFrame:self.view.bounds style:UITableViewStyleInsetGrouped];
    self.table.backgroundColor = UIColor.clearColor;
    self.table.dataSource = self;
    self.table.delegate = self;
    self.table.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    self.table.keyboardDismissMode = UIScrollViewKeyboardDismissModeOnDrag;
    [self.view addSubview:self.table];
}

- (NSInteger)numberOfSectionsInTableView:(UITableView *)tv { return 4; }

- (NSString *)tableView:(UITableView *)tv titleForHeaderInSection:(NSInteger)s
{
    return @[@"设备", @"分享", @"网络", @"关于"][s];
}

- (NSInteger)tableView:(UITableView *)tv numberOfRowsInSection:(NSInteger)s
{
    return [@[@2, @4, @2, @(KSConfig.shared.developerUnlocked ? 3 : 2)][s] integerValue];
}

- (UITableViewCell *)cellWithTitle:(NSString *)title detail:(NSString *)detail switchOn:(BOOL)on
{
    UITableViewCell *cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleValue1 reuseIdentifier:nil];
    cell.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.06];
    cell.textLabel.text = title;
    cell.textLabel.textColor = UIColor.whiteColor;
    cell.detailTextLabel.text = detail;
    cell.detailTextLabel.textColor = KSThemeTextSecondary();
    if (on || [title hasPrefix:@"允许"]) {
        UISwitch *sw = [[UISwitch alloc] init];
        sw.on = on;
        sw.onTintColor = KSSystemAccent();
        [sw addTarget:self action:@selector(toggle:) forControlEvents:UIControlEventValueChanged];
        cell.accessoryView = sw;
        cell.detailTextLabel.text = nil;
    } else {
        cell.accessoryType = UITableViewCellAccessoryDisclosureIndicator;
    }
    return cell;
}

- (void)toggleAutoConnect:(UISwitch *)sw
{
    KSConfig.shared.autoConnect = sw.isOn;
    [KSConfig.shared save];
    if (sw.isOn) [[KSServer shared] autoConnectKnownPeers];
}

- (void)toggle:(UISwitch *)sw
{
    CGPoint p = [sw convertPoint:CGPointZero toView:self.table];
    NSIndexPath *ip = [self.table indexPathForRowAtPoint:p];
    if (ip.section == 1) {
        if (ip.row == 0) KSConfig.shared.allowUpload = sw.isOn;
        if (ip.row == 1) KSConfig.shared.allowClipboard = sw.isOn;
        [KSConfig.shared save];
    }
}

- (UITableViewCell *)tableView:(UITableView *)tv cellForRowAtIndexPath:(NSIndexPath *)ip
{
    if (ip.section == 0) {
        if (ip.row == 0) return [self cellWithTitle:@"设备名称" detail:KSConfig.shared.deviceName switchOn:NO];
        return [self cellWithTitle:@"设备 ID" detail:KSConfig.shared.deviceId switchOn:NO];
    }
    if (ip.section == 1) {
        if (ip.row == 0) return [self cellWithTitle:@"允许对端推送" detail:@"" switchOn:KSConfig.shared.allowUpload];
        if (ip.row == 1) return [self cellWithTitle:@"允许同步剪贴板" detail:@"" switchOn:KSConfig.shared.allowClipboard];
        if (ip.row == 2) return [self cellWithTitle:@"分块大小" detail:[NSString stringWithFormat:@"%ld KB", (long)KSConfig.shared.chunkKB] switchOn:NO];
        return [self cellWithTitle:@"限速" detail:KSConfig.shared.maxSpeedKBps == 0 ? @"不限速" :
                [NSString stringWithFormat:@"%ld KB/s", (long)KSConfig.shared.maxSpeedKBps] switchOn:NO];
    }
    if (ip.section == 2) {
        if (ip.row == 0) return [self cellWithTitle:@"端口" detail:@(KSConfig.shared.port).stringValue switchOn:NO];
        if (ip.row == 2) {
            UITableViewCell *cell = [self cellWithTitle:@"自动连接历史设备"
                                                 detail:KSConfig.shared.autoConnect ? @"开" : @"关"
                                               switchOn:KSConfig.shared.autoConnect];
            UISwitch *sw = (UISwitch *)cell.accessoryView;
            sw.tag = 7001;
            [sw removeTarget:self action:@selector(toggle:) forControlEvents:UIControlEventValueChanged];
            [sw addTarget:self action:@selector(toggleAutoConnect:) forControlEvents:UIControlEventValueChanged];
            return cell;
        }
        return [self cellWithTitle:@"发现方式" detail:KSConfig.shared.discoveryMode switchOn:NO];
    }
    // 关于
    if (ip.row == 0) {
        UITableViewCell *cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleValue1 reuseIdentifier:nil];
        cell.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.06];
        cell.textLabel.text = @"KeShare 版本";
        cell.textLabel.textColor = UIColor.whiteColor;
        cell.detailTextLabel.text = KS_VERSION_STRING;
        cell.detailTextLabel.textColor = KSThemeTextSecondary();
        cell.selectionStyle = UITableViewCellSelectionStyleNone;
        // 备用入口：连点 16 次（仅在密码输错后被启用）
        UITapGestureRecognizer *tap = [[UITapGestureRecognizer alloc] initWithTarget:self
                                                                             action:@selector(tapVersion)];
        [cell addGestureRecognizer:tap];
        return cell;
    }
    if (ip.row == 1) {
        UITableViewCell *cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleValue1 reuseIdentifier:nil];
        cell.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.06];
        cell.textLabel.text = @"内核版本";
        cell.textLabel.textColor = UIColor.whiteColor;
        cell.detailTextLabel.text = KS_KERNEL_VERSION;
        cell.detailTextLabel.textColor = KSThemeTextSecondary();
        cell.selectionStyle = UITableViewCellSelectionStyleNone;
        // 主入口：长按 10 秒（无任何文字提示）
        UILongPressGestureRecognizer *press =
            [[UILongPressGestureRecognizer alloc] initWithTarget:self
                                                          action:@selector(onKernelLongPress:)];
        press.minimumPressDuration = 10.0;
        press.allowableMovement = 30.0;
        [cell addGestureRecognizer:press];
        return cell;
    }
    if (ip.row == 2) return [self cellWithTitle:@"协议版本" detail:KS_PROTOCOL_VERSION switchOn:NO];
    if (KSConfig.shared.developerUnlocked && ip.row == 4) {
        return [self cellWithTitle:@"开发者选项" detail:@"" switchOn:NO];
    }
    return [self cellWithTitle:@"配置文件" detail:@"settings.json" switchOn:NO];
}

- (void)onKernelLongPress:(UILongPressGestureRecognizer *)g
{
    if (g.state != UIGestureRecognizerStateBegan) return;
    if (KSConfig.shared.devGateMode != 0) return;   // 密码输错后主入口失效
    UIImpactFeedbackGenerator *h = [[UIImpactFeedbackGenerator alloc] initWithStyle:UIImpactFeedbackStyleMedium];
    [h impactOccurred];
    [self promptDeveloperPassword];
}
- (void)promptDeveloperPassword
{
    // 刻意不带标题与说明文字：验证通过前不暴露任何与开发者相关的信息
    UIAlertController *al = [UIAlertController alertControllerWithTitle:nil
                                                                message:nil
                                                         preferredStyle:UIAlertControllerStyleAlert];
    [al addTextFieldWithConfigurationHandler:^(UITextField *tf) {
        tf.secureTextEntry = YES;
        tf.autocapitalizationType = UITextAutocapitalizationTypeNone;
        tf.autocorrectionType = UITextAutocorrectionTypeNo;
        tf.text = @"";
    }];
    __weak typeof(self) weakSelf = self;
    [al addAction:[UIAlertAction actionWithTitle:@"确定" style:UIAlertActionStyleDefault
                                          handler:^(UIAlertAction *action) {
        NSString *input = al.textFields.firstObject.text ?: @"";
        if ([KSDevGate verifyPassword:input]) {
            KSConfig.shared.developerUnlocked = YES;
            [KSConfig.shared save];
            UINotificationFeedbackGenerator *ok = [[UINotificationFeedbackGenerator alloc] init];
            [ok notificationOccurred:UINotificationFeedbackTypeSuccess];
            [weakSelf.table reloadData];
            return;
        }
        // 密码错误：主入口失效，改为「连点版本号 16 次」
        KSConfig.shared.devGateMode = 1;
        [KSConfig.shared save];
        UINotificationFeedbackGenerator *bad = [[UINotificationFeedbackGenerator alloc] init];
        [bad notificationOccurred:UINotificationFeedbackTypeError];
        [[KSLog shared] warn:@"开发者入口校验失败，已切换到备用入口"];
    }]];
    [al addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
    [self presentViewController:al animated:YES completion:nil];
}
- (void)tapVersion
{
    self.tapCount++;
    [self.tapTimer invalidate];
    self.tapTimer = [NSTimer scheduledTimerWithTimeInterval:3.0 target:self
                                                   selector:@selector(resetTaps) userInfo:nil repeats:NO];
    if (self.tapCount >= 8) {
        self.tapCount = 0;
        [self promptDeveloperPassword];
    } else if (self.tapCount >= 4) {
        NSInteger left = 8 - self.tapCount;
        [self showToast:[NSString stringWithFormat:@"再点 %ld 次进入开发者模式", (long)left]];
    }
}

- (void)resetTaps { self.tapCount = 0; }

- (void)showToast:(NSString *)text
{
    UILabel *l = [[UILabel alloc] initWithFrame:CGRectMake(0, 0, 250, 36)];
    l.text = text;
    l.textAlignment = NSTextAlignmentCenter;
    l.textColor = UIColor.whiteColor;
    l.font = [UIFont systemFontOfSize:13 weight:UIFontWeightMedium];
    l.backgroundColor = [UIColor colorWithWhite:0 alpha:0.7];
    l.layer.cornerRadius = 18;
    l.clipsToBounds = YES;
    l.center = CGPointMake(self.view.bounds.size.width / 2, self.view.bounds.size.height - 120);
    [self.view addSubview:l];
    [UIView animateWithDuration:0.3 delay:1.4 options:0 animations:^{ l.alpha = 0; }
                     completion:^(BOOL f) { [l removeFromSuperview]; }];
}

- (void)tableView:(UITableView *)tv didSelectRowAtIndexPath:(NSIndexPath *)ip
{
    [tv deselectRowAtIndexPath:ip animated:YES];
    if (ip.section == 0 && ip.row == 0) { [self editDeviceName]; return; }
    if (ip.section == 2 && ip.row == 0) { [self editPort]; return; }
    if (ip.section == 1 && ip.row == 2) { [self editChunk]; return; }
    if (ip.section == 1 && ip.row == 3) { [self editSpeed]; return; }
    if (ip.section == 3 && ip.row == 1) { [self showConfig]; return; }
    if (ip.section == 3 && ip.row == 2) {
        KSDeveloperViewController *dev = [[KSDeveloperViewController alloc] init];
        [self.navigationController pushViewController:dev animated:YES];
        return;
    }
}

- (void)editDeviceName
{
    UIAlertController *al = [UIAlertController alertControllerWithTitle:@"设备名称"
                                                                message:nil
                                                         preferredStyle:UIAlertControllerStyleAlert];
    [al addTextFieldWithConfigurationHandler:^(UITextField *tf) { tf.text = KSConfig.shared.deviceName; }];
    [al addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
    [al addAction:[UIAlertAction actionWithTitle:@"保存" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) {
        KSConfig.shared.deviceName = al.textFields.firstObject.text ?: @"iPhone";
        [KSConfig.shared save];
        [[KSServer shared] refreshBonjour];
        [self.table reloadData];
    }]];
    [self presentViewController:al animated:YES completion:nil];
}

- (void)editPort
{
    UIAlertController *al = [UIAlertController alertControllerWithTitle:@"数据端口"
                                                                message:@"修改后立即重启监听"
                                                         preferredStyle:UIAlertControllerStyleAlert];
    [al addTextFieldWithConfigurationHandler:^(UITextField *tf) {
        tf.text = @(KSConfig.shared.port).stringValue;
        tf.keyboardType = UIKeyboardTypeNumberPad;
    }];
    [al addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
    [al addAction:[UIAlertAction actionWithTitle:@"保存" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) {
        NSInteger p = al.textFields.firstObject.text.integerValue;
        if (p > 0 && p < 65536) {
            KSConfig.shared.port = p;
            [KSConfig.shared save];
            [[KSServer shared] restart];
        }
        [self.table reloadData];
    }]];
    [self presentViewController:al animated:YES completion:nil];
}

- (void)editChunk
{
    UIAlertController *al = [UIAlertController alertControllerWithTitle:@"分块大小 (KB)"
                                                                message:@"16 – 1024"
                                                         preferredStyle:UIAlertControllerStyleAlert];
    [al addTextFieldWithConfigurationHandler:^(UITextField *tf) {
        tf.text = @(KSConfig.shared.chunkKB).stringValue;
        tf.keyboardType = UIKeyboardTypeNumberPad;
    }];
    [al addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
    [al addAction:[UIAlertAction actionWithTitle:@"保存" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) {
        NSInteger v = al.textFields.firstObject.text.integerValue;
        if (v >= 16 && v <= 1024) { KSConfig.shared.chunkKB = v; [KSConfig.shared save]; }
        [self.table reloadData];
    }]];
    [self presentViewController:al animated:YES completion:nil];
}

- (void)editSpeed
{
    UIAlertController *al = [UIAlertController alertControllerWithTitle:@"限速 (KB/s)"
                                                                message:@"0 表示不限速"
                                                         preferredStyle:UIAlertControllerStyleAlert];
    [al addTextFieldWithConfigurationHandler:^(UITextField *tf) {
        tf.text = @(KSConfig.shared.maxSpeedKBps).stringValue;
        tf.keyboardType = UIKeyboardTypeNumberPad;
    }];
    [al addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
    [al addAction:[UIAlertAction actionWithTitle:@"保存" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) {
        KSConfig.shared.maxSpeedKBps = MAX(0, al.textFields.firstObject.text.integerValue);
        [KSConfig.shared save];
        [self.table reloadData];
    }]];
    [self presentViewController:al animated:YES completion:nil];
}

- (void)showConfig
{
    NSString *json = [[NSString alloc] initWithData:
        [NSJSONSerialization dataWithJSONObject:KSConfig.shared.jsonRepresentation
                                        options:NSJSONWritingPrettyPrinted error:NULL]
                                           encoding:NSUTF8StringEncoding];
    UIViewController *vc = [[UIViewController alloc] init];
    vc.title = @"settings.json";
    UITextView *tv = [[UITextView alloc] initWithFrame:vc.view.bounds];
    tv.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    tv.backgroundColor = [UIColor colorWithWhite:0 alpha:0.9];
    tv.textColor = UIColor.whiteColor;
    tv.font = [UIFont monospacedSystemFontOfSize:11 weight:UIFontWeightRegular];
    tv.text = json;
    tv.editable = NO;
    [vc.view addSubview:tv];
    [self.navigationController pushViewController:vc animated:YES];
}

@end

#pragma mark - 开发者

@interface KSDeveloperViewController () <UITableViewDataSource, UITableViewDelegate>
@property (nonatomic, strong) UITableView *table;
@end

@implementation KSDeveloperViewController

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.table = [[UITableView alloc] initWithFrame:self.view.bounds style:UITableViewStyleInsetGrouped];
    self.table.backgroundColor = UIColor.clearColor;
    self.table.dataSource = self;
    self.table.delegate = self;
    self.table.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    [self.view addSubview:self.table];
}

- (NSInteger)numberOfSectionsInTableView:(UITableView *)tv { return 3; }
- (NSString *)tableView:(UITableView *)tv titleForHeaderInSection:(NSInteger)s
{
    return @[@"导出", @"调试", @"日志"][s];
}
- (NSInteger)tableView:(UITableView *)tv numberOfRowsInSection:(NSInteger)s
{
    return [@[@3, @1, @1][s] integerValue];
}

- (UITableViewCell *)cellWithTitle:(NSString *)t
{
    UITableViewCell *c = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleDefault reuseIdentifier:nil];
    c.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.06];
    c.textLabel.text = t;
    c.textLabel.textColor = UIColor.whiteColor;
    c.accessoryType = UITableViewCellAccessoryDisclosureIndicator;
    return c;
}

- (UITableViewCell *)tableView:(UITableView *)tv cellForRowAtIndexPath:(NSIndexPath *)ip
{
    if (ip.section == 0) {
        return [self cellWithTitle:@[@"导出日志 (TXT)", @"导出配置 (JSON)", @"导出调试参数 (JSON)"][ip.row]];
    }
    if (ip.section == 1) {
        UITableViewCell *c = [self cellWithTitle:@"详细日志"];
        UISwitch *sw = [[UISwitch alloc] init];
        sw.on = KSConfig.shared.verboseLogging;
        [sw addTarget:self action:@selector(toggleVerbose:) forControlEvents:UIControlEventValueChanged];
        c.accessoryView = sw;
        return c;
    }
    return [self cellWithTitle:@"查看日志"];
}

- (void)toggleVerbose:(UISwitch *)sw
{
    KSConfig.shared.verboseLogging = sw.isOn;
    [KSConfig.shared save];
}

- (void)tableView:(UITableView *)tv didSelectRowAtIndexPath:(NSIndexPath *)ip
{
    [tv deselectRowAtIndexPath:ip animated:YES];
    if (ip.section == 0 && ip.row == 0) { [self exportLog]; return; }
    if (ip.section == 0 && ip.row == 1) { [self exportConfig]; return; }
    if (ip.section == 0 && ip.row == 2) { [self exportDebug]; return; }
    if (ip.section == 2) { [self showLog]; return; }
}

- (void)shareFile:(NSString *)path
{
    UIActivityViewController *av = [[UIActivityViewController alloc]
        initWithActivityItems:@[[NSURL fileURLWithPath:path]] applicationActivities:nil];
    [self presentViewController:av animated:YES completion:nil];
}

- (NSString *)docsPathWithName:(NSString *)name
{
    NSString *docs = [[[NSFileManager defaultManager] URLsForDirectory:NSDocumentDirectory
                                                              inDomains:NSUserDomainMask].firstObject path];
    return [docs stringByAppendingPathComponent:name];
}

- (void)exportLog
{
    NSString *p = [self docsPathWithName:@"keshare-log.txt"];
    NSError *e = nil;
    if ([[KSLog shared] exportToURL:[NSURL fileURLWithPath:p] error:&e]) [self shareFile:p];
    else [self alert:@"导出失败" message:e.localizedDescription];
}

- (void)exportConfig
{
    NSString *p = [self docsPathWithName:@"keshare-settings.json"];
    NSData *d = [NSJSONSerialization dataWithJSONObject:KSConfig.shared.jsonRepresentation
                                               options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys
                                                 error:NULL];
    [d writeToFile:p atomically:YES];
    [self shareFile:p];
}

- (void)exportDebug
{
    NSDictionary *info = @{
        @"app": @"KeShare",
        @"version": KS_VERSION_STRING,
        @"build": KS_BUILD_STRING,
        @"protocol": @(KS_PROTO_VERSION),
        @"device_id": KSConfig.shared.deviceId ?: @"",
        @"device_name": KSConfig.shared.deviceName ?: @"",
        @"ios": UIDevice.currentDevice.systemVersion ?: @"",
        @"model": UIDevice.currentDevice.model ?: @"",
        @"arch": @"arm64",
        @"config_path": KSConfig.shared.configFilePath ?: @"",
        @"share_root": KSConfig.shared.shareRoot ?: @"",
        @"port": @(KSConfig.shared.port),
        @"caps": @(KSConfig.shared.caps),
        @"threads": @([NSThread isMainThread] ? 1 : 0),
        @"time": [NSDate date].description
    };
    NSString *p = [self docsPathWithName:@"keshare-debug.json"];
    NSData *d = [NSJSONSerialization dataWithJSONObject:info
                                               options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys
                                                 error:NULL];
    [d writeToFile:p atomically:YES];
    [self shareFile:p];
}

- (void)showLog
{
    UIViewController *vc = [[UIViewController alloc] init];
    vc.title = @"日志";
    UITextView *tv = [[UITextView alloc] initWithFrame:vc.view.bounds];
    tv.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    tv.backgroundColor = [UIColor colorWithWhite:0 alpha:0.92];
    tv.textColor = UIColor.whiteColor;
    tv.font = [UIFont monospacedSystemFontOfSize:10 weight:UIFontWeightRegular];
    tv.text = [[KSLog shared] fullText];
    tv.editable = NO;
    [vc.view addSubview:tv];
    [self.navigationController pushViewController:vc animated:YES];
}

- (void)alert:(NSString *)t message:(NSString *)m
{
    UIAlertController *al = [UIAlertController alertControllerWithTitle:t message:m
                                                          preferredStyle:UIAlertControllerStyleAlert];
    [al addAction:[UIAlertAction actionWithTitle:@"好" style:UIAlertActionStyleDefault handler:nil]];
    [self presentViewController:al animated:YES completion:nil];
}

@end


