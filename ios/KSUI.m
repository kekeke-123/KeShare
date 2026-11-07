//
//  KSUI.m -- 四个主页面 + 开发者页
//  KeShare 1.0
//

#import "KeShare.h"
#import "KSInternal.h"

// 底部微弹窗（实现在文件末尾的 KSErrorToast 分类中）
@interface UIViewController (KSErrorToast)
- (void)ks_showError:(NSString *)message code:(NSString *)code hint:(NSString *)hint;
@end


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

    // 下拉刷新：触发一次 UDP/BLE 重新扫描（状态码 001）
    UIRefreshControl *pull = [[UIRefreshControl alloc] init];
    [pull addTarget:self action:@selector(pullToRefresh:) forControlEvents:UIControlEventValueChanged];
    self.table.refreshControl = pull;

    [[NSNotificationCenter defaultCenter] addObserver:self.table selector:@selector(reloadData)
                                                 name:KSPeersDidChangeNotification object:nil];
    [self refresh];
}

- (void)pullToRefresh:(UIRefreshControl *)control
{
    [self showStatus:@"001 正在搜索附近设备…"];
    [[KSServer shared] refreshBonjour];
    [[KSServer shared] autoConnectKnownPeers];
    [self.table reloadData];
    [control endRefreshing];
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
    if (d.deviceId.length) [[KSServer shared].sessions setObject:self.session forKey:d.deviceId];
    [[KSLog shared] info:@"正在连接 %@", d.name];
    [self showStatus:[NSString stringWithFormat:@"002 正在握手 %@…", d.name]];

    KSDeviceDetailViewController *vc = [[KSDeviceDetailViewController alloc] init];
    vc.peer = d;
    [self.navigationController pushViewController:vc animated:YES];
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

@interface KSFilesViewController () <UITableViewDataSource, UITableViewDelegate, UIDocumentPickerDelegate>
@property (nonatomic, strong) UITableView *table;
@property (nonatomic, strong) UISegmentedControl *segment;
@property (nonatomic, strong) NSArray<NSString *> *files;
@end

@implementation KSFilesViewController

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.segment = [[UISegmentedControl alloc] initWithItems:@[@"共享目录", @"已接收"]];
    self.segment.selectedSegmentIndex = 1;
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

    self.navigationItem.rightBarButtonItems = @[
        [[UIBarButtonItem alloc] initWithImage:[UIImage systemImageNamed:@"arrow.up.arrow.down.circle"]
                                         style:UIBarButtonItemStylePlain
                                        target:self action:@selector(openTransfers)],
        [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemAdd
                                                      target:self action:@selector(importTapped)]
    ];
    self.navigationItem.leftBarButtonItem =
        [[UIBarButtonItem alloc] initWithImage:[UIImage systemImageNamed:@"magnifyingglass"]
                                         style:UIBarButtonItemStylePlain
                                        target:self action:@selector(openSearch)];
    [self reload];
}

- (void)openSearch
{
    [self.navigationController pushViewController:[[KSSearchViewController alloc] init] animated:YES];
}

- (void)viewWillAppear:(BOOL)animated
{
    [super viewWillAppear:animated];
    [self reload];
    [self updateTransfersBadge];
}

- (void)updateTransfersBadge
{
    NSInteger active = 0;
    for (KSTransfer *t in [KSTransferCenter shared].transfers) {
        if (t.state != KSTransferStateDone && t.state != KSTransferStateFailed) active++;
    }
    // 有任务在跑时把图标换成「带数字」的版本（UIBarButtonItem 没有 badgeValue）
    NSString *glyph = active > 0
        ? [NSString stringWithFormat:@"arrow.up.arrow.down.circle.fill"]
        : @"arrow.up.arrow.down.circle";
    self.navigationItem.rightBarButtonItems.firstObject.image = [UIImage systemImageNamed:glyph];
}

- (void)openTransfers
{
    [self.navigationController pushViewController:[[KSTransfersViewController alloc] init] animated:YES];
}

#pragma mark - 导入与发送

- (void)importTapped
{
    UIDocumentPickerViewController *picker =
        [[UIDocumentPickerViewController alloc] initWithDocumentTypes:@[@"public.item"]
                                                              inMode:UIDocumentPickerModeImport];
    picker.allowsMultipleSelection = YES;
    picker.delegate = self;
    [self presentViewController:picker animated:YES completion:nil];
}

- (void)documentPicker:(UIDocumentPickerViewController *)controller
didPickDocumentsAtURLs:(NSArray<NSURL *> *)urls
{
    NSString *root = KSConfig.shared.shareRoot;
    [[NSFileManager defaultManager] createDirectoryAtPath:root
                              withIntermediateDirectories:YES attributes:nil error:NULL];
    NSMutableArray<NSString *> *copied = [NSMutableArray array];
    for (NSURL *url in urls) {
        NSString *name = url.lastPathComponent;
        NSString *dest = [root stringByAppendingPathComponent:name];
        [[NSFileManager defaultManager] removeItemAtPath:dest error:NULL];
        NSError *err = nil;
        if ([[NSFileManager defaultManager] copyItemAtPath:url.path toPath:dest error:&err]) {
            [copied addObject:dest];
        } else {
            [[KSLog shared] error:@"导入失败: %@", err.localizedDescription];
        }
    }
    [self reload];
    if (copied.count == 0) return;

    // 有已连接设备就直接发，否则提示去连接
    NSArray<KSSession *> *peers = [self connectedSessions];
    if (peers.count == 0) {
        UIAlertController *al = [UIAlertController alertControllerWithTitle:@"已导入"
                                                                    message:@"文件已放入共享目录。请先在“附近设备”连接对方后再发送。"
                                                             preferredStyle:UIAlertControllerStyleAlert];
        [al addAction:[UIAlertAction actionWithTitle:@"好" style:UIAlertActionStyleDefault handler:nil]];
        [self presentViewController:al animated:YES completion:nil];
        return;
    }
    for (NSString *path in copied) {
        for (KSSession *session in peers) {
            [session uploadFileAtPath:path];
        }
    }
    [self filesToast:[NSString stringWithFormat:@"已发送 %lu 个文件到 %lu 台设备",
                     (unsigned long)copied.count, (unsigned long)peers.count]];
}

- (NSArray<KSSession *> *)connectedSessions
{
    NSMutableArray *out = [NSMutableArray array];
    for (KSSession *session in KSServer.shared.sessions.allValues) {
        if (session.link && session.link.isOpen) [out addObject:session];
    }
    return out;
}

- (void)promptSendPath:(NSString *)path
{
    NSArray<KSSession *> *peers = [self connectedSessions];
    if (peers.count == 0) {
        [self filesToast:@"还没有已连接的设备"];
        return;
    }
    UIAlertController *sheet = [UIAlertController alertControllerWithTitle:@"发送到"
                                                                  message:nil
                                                           preferredStyle:UIAlertControllerStyleActionSheet];
    for (KSSession *session in peers) {
        NSString *title = session.peer.name ?: @"未命名设备";
        [sheet addAction:[UIAlertAction actionWithTitle:title style:UIAlertActionStyleDefault
                                               handler:^(UIAlertAction *a) {
            [session uploadFileAtPath:path];
            [self filesToast:@"已加入发送队列"];
        }]];
    }
    [sheet addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
    sheet.popoverPresentationController.sourceView = self.table;
    [self presentViewController:sheet animated:YES completion:nil];
}
- (void)filesToast:(NSString *)text
{
    UILabel *label = [[UILabel alloc] initWithFrame:CGRectMake(0, 0, 260, 36)];
    label.text = text;
    label.textAlignment = NSTextAlignmentCenter;
    label.textColor = UIColor.whiteColor;
    label.font = [UIFont systemFontOfSize:13 weight:UIFontWeightMedium];
    label.backgroundColor = [UIColor colorWithWhite:0.1 alpha:0.92];
    label.layer.cornerRadius = 18;
    label.layer.cornerCurve = kCACornerCurveContinuous;
    label.clipsToBounds = YES;
    label.center = CGPointMake(self.view.bounds.size.width / 2,
                               self.view.bounds.size.height - 90);
    label.autoresizingMask = UIViewAutoresizingFlexibleTopMargin;
    [self.view addSubview:label];
    [UIView animateWithDuration:0.25 delay:1.8 options:0 animations:^{
        label.alpha = 0;
    } completion:^(BOOL done) {
        [label removeFromSuperview];
    }];
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

- (UISwipeActionsConfiguration *)tableView:(UITableView *)tv
    trailingSwipeActionsConfigurationForRowAtIndexPath:(NSIndexPath *)ip
{
    if (ip.row >= (NSInteger)self.files.count) return nil;
    NSString *name = self.files[ip.row];
    NSString *path = [[self currentDir] stringByAppendingPathComponent:name];
    __weak typeof(self) weakSelf = self;
    UIContextualAction *del = [UIContextualAction contextualActionWithStyle:UIContextualActionStyleDestructive
                                                                      title:@"删除"
                                                                    handler:^(UIContextualAction *a, UIView *v, void (^done)(BOOL)) {
        [[NSFileManager defaultManager] removeItemAtPath:path error:NULL];
        [weakSelf reload];
        done(YES);
    }];
    UIContextualAction *share = [UIContextualAction contextualActionWithStyle:UIContextualActionStyleNormal
                                                                       title:@"分享"
                                                                     handler:^(UIContextualAction *a, UIView *v, void (^done)(BOOL)) {
        UIActivityViewController *av = [[UIActivityViewController alloc]
            initWithActivityItems:@[[NSURL fileURLWithPath:path]] applicationActivities:nil];
        [weakSelf presentViewController:av animated:YES completion:nil];
        done(YES);
    }];
    share.backgroundColor = KSSystemAccent();
    return [UISwipeActionsConfiguration configurationWithActions:@[del, share]];
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
    [sheet addAction:[UIAlertAction actionWithTitle:@"发送到…" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) {
        [self promptSendPath:path];
    }]];
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

@interface KSTransfersViewController () <UITableViewDataSource, UITableViewDelegate>
@property (nonatomic, strong) UITableView *table;
@property (nonatomic, strong) UISegmentedControl *segment;
@property (nonatomic, strong) NSArray<KSTransfer *> *items;
@property (nonatomic, strong) NSTimer *ticker;
@property (nonatomic, strong) UIView *actionBar;
@end

@implementation KSTransfersViewController

- (void)presentTransferError:(NSString *)reason
{
    NSString *code = @"Unknown";
    NSString *message = reason ?: @"传输失败";
    if ([reason containsString:@"离线"] || [reason containsString:@"超时"] ||
        [reason containsString:@"连接"] || [reason containsString:@"断开"]) {
        code = @"FrontOFF";
        message = @"对方设备暂时离线！请稍后再试！";
    } else if ([reason containsString:@"校验"] || [reason containsString:@"摘要"]) {
        code = @"HashFail";
        message = @"文件完整性校验未通过！请重新传输！";
    } else if ([reason containsString:@"空间"] || [reason containsString:@"存储"]) {
        code = @"NoSpace";
        message = @"设备存储空间不足！";
    } else if ([reason containsString:@"拒绝"] || [reason containsString:@"denied"]) {
        code = @"Denied";
        message = @"对方拒绝了本次传输！";
    }
    [self ks_showError:message code:code
                 hint:@"对方传输时断开 WIFI、对方传输时没电关机、对方取消传输、对方切到后台等"];
}

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.title = @"传输任务";
    self.navigationItem.largeTitleDisplayMode = UINavigationItemLargeTitleDisplayModeNever;

    self.segment = [[UISegmentedControl alloc] initWithItems:@[@"正在传输", @"已完成"]];
    self.segment.selectedSegmentIndex = 0;
    self.segment.translatesAutoresizingMaskIntoConstraints = NO;
    [self.segment addTarget:self action:@selector(reload) forControlEvents:UIControlEventValueChanged];
    [self.view addSubview:self.segment];

    self.table = [[UITableView alloc] initWithFrame:CGRectZero style:UITableViewStyleInsetGrouped];
    self.table.backgroundColor = UIColor.clearColor;
    self.table.dataSource = self;
    self.table.delegate = self;
    self.table.rowHeight = 86;
    self.table.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:self.table];

    [NSLayoutConstraint activateConstraints:@[
        [self.segment.topAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor constant:8],
        [self.segment.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor constant:20],
        [self.segment.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor constant:-20],
        [self.table.topAnchor constraintEqualToAnchor:self.segment.bottomAnchor constant:2],
        [self.table.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor],
        [self.table.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor],
        [self.table.bottomAnchor constraintEqualToAnchor:self.view.bottomAnchor constant:-70],
    ]];

    [self buildActionBar];
    [self reload];

    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(reload)
                                                 name:KSTransfersDidChangeNotification object:nil];
}

- (void)buildActionBar
{
    UIStackView *stack = [[UIStackView alloc] init];
    stack.axis = UILayoutConstraintAxisHorizontal;
    stack.distribution = UIStackViewDistributionFillEqually;
    stack.spacing = 10;
    stack.translatesAutoresizingMaskIntoConstraints = NO;

    NSArray *titles = @[@"全部暂停", @"全部取消", @"清除已完成"];
    NSArray *selectors = @[@"pauseAll", @"cancelAll", @"clearFinished"];
    for (NSUInteger i = 0; i < titles.count; i++) {
        UIButton *b = [UIButton buttonWithType:UIButtonTypeSystem];
        [b setTitle:titles[i] forState:UIControlStateNormal];
        b.titleLabel.font = [UIFont systemFontOfSize:13 weight:UIFontWeightSemibold];
        b.backgroundColor = (i == 1)
            ? [[UIColor systemRedColor] colorWithAlphaComponent:0.18]
            : [UIColor colorWithWhite:1 alpha:0.08];
        [b setTitleColor:(i == 1) ? [UIColor systemRedColor] : UIColor.whiteColor
                forState:UIControlStateNormal];
        b.layer.cornerRadius = 14;
        b.layer.cornerCurve = kCACornerCurveContinuous;
        b.tag = (NSInteger)i + 1;
        SEL sel = NSSelectorFromString(selectors[i]);
        [b addTarget:self action:sel forControlEvents:UIControlEventTouchUpInside];
        [stack addArrangedSubview:b];
    }
    [self.view addSubview:stack];
    [NSLayoutConstraint activateConstraints:@[
        [stack.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor constant:20],
        [stack.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor constant:-20],
        [stack.bottomAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.bottomAnchor constant:-8],
        [stack.heightAnchor constraintEqualToConstant:44],
    ]];
}

- (void)viewWillAppear:(BOOL)animated
{
    [super viewWillAppear:animated];
    [self reload];
    if (!self.ticker) {
        __weak typeof(self) weakSelf = self;
        self.ticker = [NSTimer scheduledTimerWithTimeInterval:0.8 repeats:YES
                                                      block:^(NSTimer *t) {
            [weakSelf reload];
        }];
    }
}

- (void)viewWillDisappear:(BOOL)animated
{
    [super viewWillDisappear:animated];
    [self.ticker invalidate];
    self.ticker = nil;
}

- (void)pauseAll
{
    for (KSTransfer *t in [KSTransferCenter shared].transfers) {
        if (t.state == KSTransferStateRunning || t.state == KSTransferStateWaiting) {
            [[KSTransferCenter shared] pauseWithId:t.transferId];
        }
    }
    [self reload];
}

- (void)cancelAll
{
    NSMutableArray *active = [NSMutableArray array];
    for (KSTransfer *t in [KSTransferCenter shared].transfers) {
        if (t.state == KSTransferStateDone || t.state == KSTransferStateFailed) continue;
        [active addObject:t];
    }
    if (active.count == 0) return;
    UIAlertController *al = [UIAlertController alertControllerWithTitle:@"取消全部传输？"
                                                                message:@"已传输的部分会被丢弃"
                                                         preferredStyle:UIAlertControllerStyleActionSheet];
    [al addAction:[UIAlertAction actionWithTitle:@"取消全部" style:UIAlertActionStyleDestructive
                                        handler:^(UIAlertAction *a) {
        for (KSTransfer *t in active) [[KSTransferCenter shared] cancelWithId:t.transferId];
        [self reload];
    }]];
    [al addAction:[UIAlertAction actionWithTitle:@"再想想" style:UIAlertActionStyleCancel handler:nil]];
    al.popoverPresentationController.sourceView = self.table;
    [self presentViewController:al animated:YES completion:nil];
}

- (void)clearFinished
{
    [[KSTransferCenter shared] clearFinished];
    [self reload];
}

- (void)reload
{
    // 手指正在滑动/减速时不要重载，否则会打断左滑手势
    if (self.table.isDragging || self.table.isDecelerating) return;
    BOOL finished = self.segment.selectedSegmentIndex == 1;
    NSMutableArray *out = [NSMutableArray array];
    for (KSTransfer *t in [KSTransferCenter shared].transfers) {
        BOOL isFinished = (t.state == KSTransferStateDone || t.state == KSTransferStateFailed);
        if (isFinished == finished) [out addObject:t];
    }
    self.items = out;
    [self.table reloadData];
}

#pragma mark - Table

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
        cell.textLabel.font = [UIFont systemFontOfSize:14 weight:UIFontWeightSemibold];
        cell.detailTextLabel.textColor = KSThemeTextSecondary();
        cell.detailTextLabel.font = [UIFont systemFontOfSize:11];
        cell.selectionStyle = UITableViewCellSelectionStyleNone;

        UIProgressView *pv = [[UIProgressView alloc] initWithProgressViewStyle:UIProgressViewStyleDefault];
        pv.tag = 99;
        pv.progressTintColor = KSSystemAccent();
        pv.trackTintColor = [UIColor colorWithWhite:1 alpha:0.14];
        pv.translatesAutoresizingMaskIntoConstraints = NO;
        [cell.contentView addSubview:pv];

        UILabel *speed = [[UILabel alloc] init];
        speed.tag = 98;
        speed.font = [UIFont monospacedDigitSystemFontOfSize:11 weight:UIFontWeightMedium];
        speed.textColor = KSThemeTextSecondary();
        speed.textAlignment = NSTextAlignmentRight;
        speed.translatesAutoresizingMaskIntoConstraints = NO;
        [cell.contentView addSubview:speed];

        [NSLayoutConstraint activateConstraints:@[
            [pv.leadingAnchor constraintEqualToAnchor:cell.textLabel.leadingAnchor],
            [pv.trailingAnchor constraintEqualToAnchor:cell.contentView.trailingAnchor constant:-18],
            [pv.bottomAnchor constraintEqualToAnchor:cell.contentView.bottomAnchor constant:-10],
            [speed.trailingAnchor constraintEqualToAnchor:cell.contentView.trailingAnchor constant:-18],
            [speed.centerYAnchor constraintEqualToAnchor:cell.detailTextLabel.centerYAnchor],
        ]];
    }
    UIProgressView *pv = (UIProgressView *)[cell.contentView viewWithTag:99];
    UILabel *speed = (UILabel *)[cell.contentView viewWithTag:98];

    if (self.items.count == 0) {
        cell.textLabel.text = @"还没有传输任务";
        cell.detailTextLabel.text = @"从「附近设备」连接后即可收发";
        cell.imageView.image = [UIImage systemImageNamed:@"arrow.up.arrow.down"];
        pv.hidden = YES;
        speed.text = @"";
        return cell;
    }
    KSTransfer *t = self.items[ip.row];
    pv.hidden = NO;
    cell.textLabel.text = t.fileName;
    cell.detailTextLabel.text = [NSString stringWithFormat:@"%@ · %@ · %@ / %@ · %@",
                                 t.peerName ?: @"—",
                                 t.direction == KSTransferDirectionUpload ? @"发送" : @"接收",
                                 KSFormatBytes(t.done), KSFormatBytes(t.total), t.stateLabel];
    cell.imageView.image = [UIImage systemImageNamed:
        t.direction == KSTransferDirectionUpload ? @"arrow.up.circle.fill" : @"arrow.down.circle.fill"];
    [pv setProgress:(float)t.progress animated:YES];
    speed.text = t.state == KSTransferStatePaused ? @"已暂停" : t.speedLabel;
    return cell;
}

- (UISwipeActionsConfiguration *)tableView:(UITableView *)tv
    trailingSwipeActionsConfigurationForRowAtIndexPath:(NSIndexPath *)ip
{
    if (ip.row >= (NSInteger)self.items.count) return nil;
    KSTransfer *t = self.items[ip.row];
    BOOL active = (t.state != KSTransferStateDone && t.state != KSTransferStateFailed);
    if (!active) return nil;
    __weak typeof(self) weakSelf = self;
    UIContextualAction *pause = [UIContextualAction contextualActionWithStyle:UIContextualActionStyleNormal
                                                                       title:t.paused ? @"继续" : @"暂停"
                                                                     handler:^(UIContextualAction *a, UIView *v, void (^done)(BOOL)) {
        if (t.paused) [[KSTransferCenter shared] resumeWithId:t.transferId];
        else [[KSTransferCenter shared] pauseWithId:t.transferId];
        [weakSelf reload];
        done(YES);
    }];
    UIContextualAction *cancel = [UIContextualAction contextualActionWithStyle:UIContextualActionStyleDestructive
                                                                        title:@"取消"
                                                                      handler:^(UIContextualAction *a, UIView *v, void (^done)(BOOL)) {
        [[KSTransferCenter shared] cancelWithId:t.transferId];
        [weakSelf reload];
        done(YES);
    }];
    return [UISwipeActionsConfiguration configurationWithActions:@[cancel, pause]];
}

- (void)tableView:(UITableView *)tv didSelectRowAtIndexPath:(NSIndexPath *)ip
{
    [tv deselectRowAtIndexPath:ip animated:YES];
    if (ip.row >= (NSInteger)self.items.count) return;
    KSTransferDetailViewController *vc = [[KSTransferDetailViewController alloc] init];
    vc.transfer = self.items[ip.row];
    [self.navigationController pushViewController:vc animated:YES];
}

@end

#pragma mark - 传输任务详情

@interface KSTransferDetailViewController () <UITableViewDataSource, UITableViewDelegate>
@property (nonatomic, strong) UITableView *table;
@property (nonatomic, strong) NSTimer *ticker;
@property (nonatomic, strong) NSArray<NSArray *> *rows;
@end

@implementation KSTransferDetailViewController

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.title = @"任务详情";
    self.navigationItem.largeTitleDisplayMode = UINavigationItemLargeTitleDisplayModeNever;

    self.table = [[UITableView alloc] initWithFrame:self.view.bounds style:UITableViewStyleInsetGrouped];
    self.table.backgroundColor = UIColor.clearColor;
    self.table.dataSource = self;
    self.table.delegate = self;
    self.table.rowHeight = 48;
    self.table.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    [self.view addSubview:self.table];

    [self rebuildRows];
    __weak typeof(self) weakSelf = self;
    self.ticker = [NSTimer scheduledTimerWithTimeInterval:0.6 repeats:YES block:^(NSTimer *t) {
        [weakSelf rebuildRows];
        [weakSelf.table reloadData];
    }];
}

- (void)viewWillAppear:(BOOL)animated
{
    [super viewWillAppear:animated];
    [self rebuildRows];
    if (!self.ticker) {
        __weak typeof(self) weakSelf = self;
        self.ticker = [NSTimer scheduledTimerWithTimeInterval:0.6 repeats:YES block:^(NSTimer *t) {
            [weakSelf rebuildRows];
            [weakSelf.table reloadData];
        }];
    }
}

- (void)viewWillDisappear:(BOOL)animated
{
    [super viewWillDisappear:animated];
    [self.ticker invalidate];
    self.ticker = nil;
}

- (void)rebuildRows
{
    KSTransfer *t = self.transfer;
    if (!t) return;
    NSString *verify = t.verified ? @"SHA-256 校验通过"
                     : (t.state == KSTransferStateFailed ? @"未通过 / 未完成"
                        : (t.state == KSTransferStateVerifying ? @"正在校验…" : @"等待校验"));
    NSString *progress = [NSString stringWithFormat:@"%@ / %@（%.1f%%）",
                          KSFormatBytes(t.done), KSFormatBytes(t.total), t.progress * 100.0];
    NSString *started = t.startedAt ? [NSDateFormatter localizedStringFromDate:t.startedAt
                                                                    dateStyle:NSDateFormatterNoStyle
                                                                    timeStyle:NSDateFormatterMediumStyle]
                                    : @"—";
    self.rows = @[
        @[@"文件名", t.fileName ?: @"—"],
        @[@"方向", t.direction == KSTransferDirectionUpload ? @"发送" : @"接收"],
        @[@"对端设备", t.peerName ?: @"—"],
        @[@"进度", progress],
        @[@"速度", t.state == KSTransferStatePaused ? @"已暂停" : t.speedLabel],
        @[@"状态", t.stateLabel ?: @"—"],
        @[@"完整性", verify],
        @[@"开始时间", started],
        @[@"保存位置", t.path ?: @"—"],
        @[@"任务 ID", t.transferId ?: @"—"],
    ];
    self.navigationItem.rightBarButtonItem = nil;
    if (t.state != KSTransferStateDone && t.state != KSTransferStateFailed) {
        self.navigationItem.rightBarButtonItem =
            [[UIBarButtonItem alloc] initWithTitle:(t.paused ? @"继续" : @"暂停")
                                             style:UIBarButtonItemStylePlain
                                            target:self action:@selector(togglePause)];
    }
}

- (void)togglePause
{
    KSTransfer *t = self.transfer;
    if (!t) return;
    if (t.paused) [[KSTransferCenter shared] resumeWithId:t.transferId];
    else [[KSTransferCenter shared] pauseWithId:t.transferId];
    [self rebuildRows];
    [self.table reloadData];
}

- (NSInteger)numberOfSectionsInTableView:(UITableView *)tv { return 2; }

- (NSInteger)tableView:(UITableView *)tv numberOfRowsInSection:(NSInteger)section
{
    return section == 0 ? (NSInteger)self.rows.count : 3;
}

- (UITableViewCell *)tableView:(UITableView *)tv cellForRowAtIndexPath:(NSIndexPath *)ip
{
    UITableViewCell *cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleValue1
                                                   reuseIdentifier:nil];
    cell.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.06];
    cell.textLabel.font = [UIFont systemFontOfSize:13];
    cell.textLabel.textColor = KSThemeTextSecondary();
    cell.detailTextLabel.font = [UIFont systemFontOfSize:13 weight:UIFontWeightMedium];
    cell.detailTextLabel.textColor = UIColor.whiteColor;
    cell.selectionStyle = UITableViewCellSelectionStyleNone;

    if (ip.section == 0) {
        cell.textLabel.text = self.rows[ip.row][0];
        cell.detailTextLabel.text = self.rows[ip.row][1];
        cell.detailTextLabel.numberOfLines = 2;
        return cell;
    }
    NSArray *titles = @[@"取消任务", @"分享文件", @"复制保存路径"];
    cell.textLabel.text = titles[ip.row];
    cell.textLabel.font = [UIFont systemFontOfSize:14 weight:UIFontWeightSemibold];
    cell.textLabel.textColor = (ip.row == 0) ? [UIColor systemRedColor] : KSSystemAccent();
    cell.detailTextLabel.text = @"";
    cell.selectionStyle = UITableViewCellSelectionStyleDefault;
    cell.textLabel.textAlignment = NSTextAlignmentCenter;
    return cell;
}

- (void)tableView:(UITableView *)tv didSelectRowAtIndexPath:(NSIndexPath *)ip
{
    [tv deselectRowAtIndexPath:ip animated:YES];
    if (ip.section != 1) return;
    KSTransfer *t = self.transfer;
    if (ip.row == 0) {
        UIAlertController *al = [UIAlertController alertControllerWithTitle:@"取消该任务？"
                                                                    message:@"已传输的部分会被丢弃"
                                                             preferredStyle:UIAlertControllerStyleAlert];
        [al addAction:[UIAlertAction actionWithTitle:@"取消任务" style:UIAlertActionStyleDestructive
                                            handler:^(UIAlertAction *a) {
            [[KSTransferCenter shared] cancelWithId:t.transferId];
            [self.navigationController popViewControllerAnimated:YES];
        }]];
        [al addAction:[UIAlertAction actionWithTitle:@"再想想" style:UIAlertActionStyleCancel handler:nil]];
        [self presentViewController:al animated:YES completion:nil];
    } else if (ip.row == 1) {
        if (t.path.length && [[NSFileManager defaultManager] fileExistsAtPath:t.path]) {
            UIActivityViewController *av = [[UIActivityViewController alloc]
                initWithActivityItems:@[[NSURL fileURLWithPath:t.path]] applicationActivities:nil];
            [self presentViewController:av animated:YES completion:nil];
        } else {
            [self ks_showError:@"文件还不存在" code:@"NoFile" hint:@"传输完成并通过校验后才能分享"];
        }
    } else {
        UIPasteboard.generalPasteboard.string = t.path ?: @"";
    }
}

@end

#pragma mark - 设备（手动连接）

@interface KSAddDeviceViewController : KSViewController <UITextFieldDelegate>
@property (nonatomic, copy) void (^onSaved)(KSDevice *device, BOOL connect);
@property (nonatomic, strong) UITextField *nameField;
@property (nonatomic, strong) UITextField *hostField;
@property (nonatomic, strong) UITextField *portField;
@end

@implementation KSAddDeviceViewController

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.title = @"添加设备";
    self.navigationItem.largeTitleDisplayMode = UINavigationItemLargeTitleDisplayModeNever;

    self.nameField = [self fieldWithPlaceholder:@"备注名（可选）" keyboard:UIKeyboardTypeDefault];
    self.hostField = [self fieldWithPlaceholder:@"IP 地址，例如 192.168.1.100" keyboard:UIKeyboardTypeDecimalPad];
    self.portField = [self fieldWithPlaceholder:@"端口" keyboard:UIKeyboardTypeNumberPad];
    self.portField.text = @(KSConfig.shared.port).stringValue;

    UIStackView *stack = [[UIStackView alloc] initWithArrangedSubviews:@[
        self.nameField, self.hostField, self.portField
    ]];
    stack.axis = UILayoutConstraintAxisVertical;
    stack.spacing = 10;
    stack.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:stack];

    UIButton *save = [UIButton buttonWithType:UIButtonTypeSystem];
    [save setTitle:@"保存" forState:UIControlStateNormal];
    save.backgroundColor = [UIColor colorWithWhite:1 alpha:0.10];
    [save setTitleColor:UIColor.whiteColor forState:UIControlStateNormal];
    save.layer.cornerRadius = 14;
    save.layer.cornerCurve = kCACornerCurveContinuous;
    [save addTarget:self action:@selector(saveOnly) forControlEvents:UIControlEventTouchUpInside];

    UIButton *saveConnect = [UIButton buttonWithType:UIButtonTypeSystem];
    [saveConnect setTitle:@"保存并连接" forState:UIControlStateNormal];
    saveConnect.backgroundColor = KSSystemAccent();
    [saveConnect setTitleColor:UIColor.blackColor forState:UIControlStateNormal];
    saveConnect.titleLabel.font = [UIFont systemFontOfSize:15 weight:UIFontWeightSemibold];
    saveConnect.layer.cornerRadius = 14;
    saveConnect.layer.cornerCurve = kCACornerCurveContinuous;
    [saveConnect addTarget:self action:@selector(saveAndConnect) forControlEvents:UIControlEventTouchUpInside];

    UIStackView *buttons = [[UIStackView alloc] initWithArrangedSubviews:@[save, saveConnect]];
    buttons.axis = UILayoutConstraintAxisHorizontal;
    buttons.distribution = UIStackViewDistributionFillEqually;
    buttons.spacing = 10;
    buttons.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:buttons];

    [NSLayoutConstraint activateConstraints:@[
        [stack.topAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor constant:20],
        [stack.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor constant:20],
        [stack.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor constant:-20],
        [buttons.topAnchor constraintEqualToAnchor:stack.bottomAnchor constant:20],
        [buttons.leadingAnchor constraintEqualToAnchor:stack.leadingAnchor],
        [buttons.trailingAnchor constraintEqualToAnchor:stack.trailingAnchor],
        [buttons.heightAnchor constraintEqualToConstant:46],
    ]];
}

- (UITextField *)fieldWithPlaceholder:(NSString *)placeholder keyboard:(UIKeyboardType)kb
{
    UITextField *f = [[UITextField alloc] init];
    f.placeholder = placeholder;
    f.keyboardType = kb;
    f.textColor = UIColor.whiteColor;
    f.attributedPlaceholder = [[NSAttributedString alloc]
        initWithString:placeholder
            attributes:@{NSForegroundColorAttributeName: [UIColor colorWithWhite:1 alpha:0.4]}];
    f.backgroundColor = [UIColor colorWithWhite:1 alpha:0.08];
    f.layer.cornerRadius = 12;
    f.layer.cornerCurve = kCACornerCurveContinuous;
    f.leftView = [[UIView alloc] initWithFrame:CGRectMake(0, 0, 12, 44)];
    f.leftViewMode = UITextFieldViewModeAlways;
    f.translatesAutoresizingMaskIntoConstraints = NO;
    [f.heightAnchor constraintEqualToConstant:44].active = YES;
    return f;
}

- (void)commit:(BOOL)connect
{
    NSString *host = [self.hostField.text stringByTrimmingCharactersInSet:
                      [NSCharacterSet whitespaceCharacterSet]];
    if (host.length == 0) {
        [self ks_showError:@"IP 地址不能为空" code:@"BadAddr" hint:@"示例：192.168.1.100"];
        return;
    }
    NSInteger port = self.portField.text.integerValue;
    if (port <= 0 || port > 65535) port = KSConfig.shared.port;

    KSDevice *d = [[KSDevice alloc] init];
    d.deviceId = [NSString stringWithFormat:@"manual-%@", host];
    d.name = self.nameField.text.length ? self.nameField.text : host;
    d.host = host;
    d.port = (uint16_t)port;
    d.kind = KSDeviceKindWindows;
    [KSConfig.shared rememberDevice:d];
    [[KSServer shared] addPeer:d];
    if (self.onSaved) self.onSaved(d, connect);
    [self.navigationController popViewControllerAnimated:YES];
}

- (void)saveOnly { [self commit:NO]; }
- (void)saveAndConnect { [self commit:YES]; }

@end

@interface KSManualDevicesViewController () <UITableViewDataSource, UITableViewDelegate>
@property (nonatomic, strong) UITableView *table;
@property (nonatomic, strong) NSArray<NSDictionary *> *items;
@end

@implementation KSManualDevicesViewController

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.table = [[UITableView alloc] initWithFrame:self.view.bounds style:UITableViewStyleInsetGrouped];
    self.table.backgroundColor = UIColor.clearColor;
    self.table.dataSource = self;
    self.table.delegate = self;
    self.table.rowHeight = 62;
    self.table.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    [self.view addSubview:self.table];

    self.navigationItem.rightBarButtonItem =
        [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemAdd
                                                      target:self action:@selector(addDevice)];
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(reload)
                                                 name:KSPeersDidChangeNotification object:nil];
    [self reload];
}

- (void)reload
{
    self.items = [KSConfig.shared knownDevices] ?: @[];
    [self.table reloadData];
}

- (void)addDevice
{
    KSAddDeviceViewController *vc = [[KSAddDeviceViewController alloc] init];
    __weak typeof(self) weakSelf = self;
    vc.onSaved = ^(KSDevice *device, BOOL connect) {
        [weakSelf reload];
        if (connect) [weakSelf connectDevice:device];
    };
    [self.navigationController pushViewController:vc animated:YES];
}

- (void)connectDevice:(KSDevice *)d
{
    KSSession *session = [[KSSession alloc] initWithPeer:d];
    [session startAsInitiator:YES];
    [[KSServer shared].sessions setObject:session forKey:d.deviceId ?: @""];
    [[KSLog shared] info:@"手动连接 %@ (%@:%u)", d.name, d.host, d.port];
    [self showHint:[NSString stringWithFormat:@"正在连接 %@ …", d.name]];
}

- (void)showHint:(NSString *)text
{
    static NSInteger tag = 9210;
    [[self.view viewWithTag:tag] removeFromSuperview];
    UILabel *label = [[UILabel alloc] initWithFrame:CGRectZero];
    label.tag = tag;
    label.text = text;
    label.textAlignment = NSTextAlignmentCenter;
    label.textColor = UIColor.whiteColor;
    label.font = [UIFont systemFontOfSize:13 weight:UIFontWeightMedium];
    label.backgroundColor = [UIColor colorWithWhite:0.1 alpha:0.92];
    label.layer.cornerRadius = 16;
    label.layer.cornerCurve = kCACornerCurveContinuous;
    label.clipsToBounds = YES;
    label.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:label];
    [NSLayoutConstraint activateConstraints:@[
        [label.centerXAnchor constraintEqualToAnchor:self.view.centerXAnchor],
        [label.bottomAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.bottomAnchor constant:-24],
        [label.widthAnchor constraintEqualToConstant:260],
        [label.heightAnchor constraintEqualToConstant:36],
    ]];
    [UIView animateWithDuration:0.3 delay:2.2 options:0 animations:^{
        label.alpha = 0;
    } completion:^(BOOL done) {
        [label removeFromSuperview];
    }];
}

- (NSInteger)tableView:(UITableView *)tv numberOfRowsInSection:(NSInteger)section
{
    return MAX(1, (NSInteger)self.items.count);
}

- (UITableViewCell *)tableView:(UITableView *)tv cellForRowAtIndexPath:(NSIndexPath *)ip
{
    static NSString *cid = @"manual";
    UITableViewCell *cell = [tv dequeueReusableCellWithIdentifier:cid];
    if (!cell) {
        cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleSubtitle reuseIdentifier:cid];
        cell.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.06];
        cell.textLabel.textColor = UIColor.whiteColor;
        cell.detailTextLabel.textColor = KSThemeTextSecondary();
        cell.imageView.image = [UIImage systemImageNamed:@"desktopcomputer"];
    }
    if (self.items.count == 0) {
        cell.textLabel.text = @"还没有手动添加的设备";
        cell.detailTextLabel.text = @"点右上角 ＋ 添加 IP 地址";
        cell.accessoryType = UITableViewCellAccessoryNone;
        return cell;
    }
    NSDictionary *item = self.items[ip.row];
    cell.textLabel.text = item[@"name"] ?: item[@"did"];
    cell.detailTextLabel.text = [NSString stringWithFormat:@"%@:%@",
                                 item[@"host"] ?: @"—", item[@"port"] ?: @47377];
    cell.accessoryType = UITableViewCellAccessoryDisclosureIndicator;
    return cell;
}

- (void)tableView:(UITableView *)tv didSelectRowAtIndexPath:(NSIndexPath *)ip
{
    [tv deselectRowAtIndexPath:ip animated:YES];
    if (ip.row >= (NSInteger)self.items.count) return;
    NSDictionary *item = self.items[ip.row];
    KSDevice *d = [[KSDevice alloc] init];
    d.deviceId = item[@"did"] ?: @"";
    d.name = item[@"name"] ?: d.deviceId;
    d.host = item[@"host"] ?: @"";
    d.port = (uint16_t)[item[@"port"] integerValue];
    d.kind = KSDeviceKindWindows;
    [[KSServer shared] addPeer:d];
    [self connectDevice:d];
}

- (UISwipeActionsConfiguration *)tableView:(UITableView *)tv
    trailingSwipeActionsConfigurationForRowAtIndexPath:(NSIndexPath *)ip
{
    if (ip.row >= (NSInteger)self.items.count) return nil;
    NSDictionary *item = self.items[ip.row];
    __weak typeof(self) weakSelf = self;
    UIContextualAction *del = [UIContextualAction contextualActionWithStyle:UIContextualActionStyleDestructive
                                                                      title:@"删除"
                                                                    handler:^(UIContextualAction *a, UIView *v, void (^done)(BOOL)) {
        [KSConfig.shared forgetDeviceWithId:item[@"did"] ?: @""];
        [weakSelf reload];
        done(YES);
    }];
    return [UISwipeActionsConfiguration configurationWithActions:@[del]];
}

@end

#pragma mark - 设备详情

@interface KSDeviceDetailViewController () <UITableViewDataSource, UITableViewDelegate, UIDocumentPickerDelegate>
@property (nonatomic, strong) UITableView *table;
@property (nonatomic, strong) KSSession *session;
@property (nonatomic, strong) NSArray<KSTransfer *> *recent;
@end

@implementation KSDeviceDetailViewController

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.title = self.peer.name ?: @"设备";
    self.navigationItem.largeTitleDisplayMode = UINavigationItemLargeTitleDisplayModeNever;

    self.table = [[UITableView alloc] initWithFrame:self.view.bounds style:UITableViewStyleInsetGrouped];
    self.table.backgroundColor = UIColor.clearColor;
    self.table.dataSource = self;
    self.table.delegate = self;
    self.table.rowHeight = 56;
    self.table.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    [self.view addSubview:self.table];

    self.session = [[KSServer shared].sessions objectForKey:self.peer.deviceId ?: @""];
    if (!self.session) {
        self.session = [[KSSession alloc] initWithPeer:self.peer];
        [self.session startAsInitiator:YES];
        if (self.peer.deviceId.length) {
            [[KSServer shared].sessions setObject:self.session forKey:self.peer.deviceId];
        }
    }
    [self reloadRecent];
}

- (void)viewWillAppear:(BOOL)animated
{
    [super viewWillAppear:animated];
    [self reloadRecent];
}

- (void)reloadRecent
{
    NSMutableArray *mine = [NSMutableArray array];
    for (KSTransfer *t in [KSTransferCenter shared].transfers) {
        if (t.peerName.length && [t.peerName isEqualToString:self.peer.name ?: @""]) {
            [mine addObject:t];
        }
    }
    self.recent = [mine count] > 5 ? [mine subarrayWithRange:NSMakeRange(0, 5)] : mine;
    [self.table reloadData];
}

- (NSInteger)numberOfSectionsInTableView:(UITableView *)tv { return 3; }

- (NSString *)tableView:(UITableView *)tv titleForHeaderInSection:(NSInteger)s
{
    return @[@"连接", @"可用操作", @"最近传输"][s];
}

- (NSInteger)tableView:(UITableView *)tv numberOfRowsInSection:(NSInteger)s
{
    if (s == 0) return 3;
    if (s == 1) return 4;
    return MAX(1, (NSInteger)self.recent.count);
}

- (UITableViewCell *)tableView:(UITableView *)tv cellForRowAtIndexPath:(NSIndexPath *)ip
{
    if (ip.section == 0) {
        NSArray *rows = @[
            @[@"设备名称", self.peer.name ?: @"—"],
            @[@"类型", [self.peer kindLabel]],
            @[@"地址", [NSString stringWithFormat:@"%@:%u",
                        self.peer.host ?: @"—", self.peer.port]]
        ];
        NSArray *row = rows[ip.row];
        UITableViewCell *cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleValue1
                                                       reuseIdentifier:nil];
        cell.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.06];
        cell.textLabel.text = row[0];
        cell.textLabel.textColor = UIColor.whiteColor;
        cell.detailTextLabel.text = row[1];
        cell.detailTextLabel.textColor = KSThemeTextSecondary();
        cell.selectionStyle = UITableViewCellSelectionStyleNone;
        return cell;
    }
    if (ip.section == 1) {
        NSArray *titles = @[@"发送文件", @"浏览对方文件", @"同步剪贴板", @"断开连接"];
        UITableViewCell *cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleDefault
                                                       reuseIdentifier:nil];
        cell.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.06];
        cell.textLabel.text = titles[ip.row];
        cell.textLabel.textColor = (ip.row == 3) ? [UIColor systemRedColor] : UIColor.whiteColor;
        cell.textLabel.font = [UIFont systemFontOfSize:14 weight:UIFontWeightSemibold];
        cell.accessoryType = UITableViewCellAccessoryDisclosureIndicator;
        cell.selectionStyle = UITableViewCellSelectionStyleDefault;
        return cell;
    }
    UITableViewCell *cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleSubtitle
                                                   reuseIdentifier:nil];
    cell.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.06];
    if (self.recent.count == 0) {
        cell.textLabel.text = @"还没有传输记录";
        cell.textLabel.textColor = KSThemeTextSecondary();
        cell.detailTextLabel.text = @"点上面「发送文件」开始";
        cell.detailTextLabel.textColor = KSThemeTextSecondary();
        cell.selectionStyle = UITableViewCellSelectionStyleNone;
        return cell;
    }
    KSTransfer *t = self.recent[ip.row];
    cell.textLabel.text = t.fileName;
    cell.textLabel.textColor = UIColor.whiteColor;
    cell.detailTextLabel.text = [NSString stringWithFormat:@"%@ · %@",
                                 t.stateLabel, KSFormatBytes(t.total)];
    cell.detailTextLabel.textColor = KSThemeTextSecondary();
    cell.accessoryType = UITableViewCellAccessoryDisclosureIndicator;
    cell.selectionStyle = UITableViewCellSelectionStyleDefault;
    return cell;
}

- (void)tableView:(UITableView *)tv didSelectRowAtIndexPath:(NSIndexPath *)ip
{
    [tv deselectRowAtIndexPath:ip animated:YES];
    if (ip.section == 1) {
        if (ip.row == 0) [self pickFile];
        else if (ip.row == 1) [self openRemoteFiles];
        else if (ip.row == 2) [self syncClipboard];
        else [self disconnect];
    } else if (ip.section == 2 && self.recent.count) {
        KSTransferDetailViewController *vc = [[KSTransferDetailViewController alloc] init];
        vc.transfer = self.recent[ip.row];
        [self.navigationController pushViewController:vc animated:YES];
    }
}

- (void)pickFile
{
    UIDocumentPickerViewController *picker =
        [[UIDocumentPickerViewController alloc] initWithDocumentTypes:@[@"public.item"]
                                                              inMode:UIDocumentPickerModeImport];
    picker.allowsMultipleSelection = YES;
    picker.delegate = self;
    [self presentViewController:picker animated:YES completion:nil];
}

- (void)documentPicker:(UIDocumentPickerViewController *)controller
didPickDocumentsAtURLs:(NSArray<NSURL *> *)urls
{
    NSString *root = KSConfig.shared.shareRoot;
    [[NSFileManager defaultManager] createDirectoryAtPath:root
                              withIntermediateDirectories:YES attributes:nil error:NULL];
    for (NSURL *url in urls) {
        NSString *dest = [root stringByAppendingPathComponent:url.lastPathComponent];
        [[NSFileManager defaultManager] removeItemAtPath:dest error:NULL];
        if ([[NSFileManager defaultManager] copyItemAtPath:url.path toPath:dest error:NULL]) {
            [self.session uploadFileAtPath:dest];
        }
    }
    [self reloadRecent];
}

- (void)openRemoteFiles
{
    if (!self.session) return;
    KSRemoteFilesViewController *vc = [[KSRemoteFilesViewController alloc] init];
    vc.session = self.session;
    vc.remotePath = @"";
    [self.navigationController pushViewController:vc animated:YES];
}

- (void)syncClipboard
{
    [self.session sendClipboardText:UIPasteboard.generalPasteboard.string ?: @""];
    [self ks_showError:@"已发送剪贴板内容" code:@"Clip" hint:@"对端会覆盖自己的剪贴板"];
}

- (void)disconnect
{
    [self.session close];
    [[KSServer shared].sessions removeObjectForKey:self.peer.deviceId ?: @""];
    [self.navigationController popViewControllerAnimated:YES];
}

@end

#pragma mark - 浏览对端文件

@interface KSRemoteFilesViewController () <UITableViewDataSource, UITableViewDelegate>
@property (nonatomic, strong) UITableView *table;
@property (nonatomic, strong) NSArray<NSDictionary *> *files;
@property (nonatomic, strong) NSTimer *poller;
@property (nonatomic, assign) NSInteger ticks;
@end

@implementation KSRemoteFilesViewController

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.title = self.remotePath.length ? self.remotePath.lastPathComponent : @"对方文件";
    self.navigationItem.largeTitleDisplayMode = UINavigationItemLargeTitleDisplayModeNever;
    self.table = [[UITableView alloc] initWithFrame:self.view.bounds style:UITableViewStyleInsetGrouped];
    self.table.backgroundColor = UIColor.clearColor;
    self.table.dataSource = self;
    self.table.delegate = self;
    self.table.rowHeight = 58;
    self.table.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    [self.view addSubview:self.table];

    [self.session requestListingPath:self.remotePath];
    __weak typeof(self) weakSelf = self;
    self.ticks = 0;
    self.poller = [NSTimer scheduledTimerWithTimeInterval:0.3 repeats:YES block:^(NSTimer *t) {
        [weakSelf poll];
    }];
}

- (void)viewWillDisappear:(BOOL)animated
{
    [super viewWillDisappear:animated];
    [self.poller invalidate];
    self.poller = nil;
}

- (void)poll
{
    self.ticks++;
    NSArray *listing = self.session.lastListing;
    if (listing.count || self.ticks > 8) {
        self.files = listing ?: @[];
        [self.table reloadData];
        [self.poller invalidate];
        self.poller = nil;
    }
}

- (NSInteger)tableView:(UITableView *)tv numberOfRowsInSection:(NSInteger)section
{
    return MAX(1, (NSInteger)self.files.count);
}

- (UITableViewCell *)tableView:(UITableView *)tv cellForRowAtIndexPath:(NSIndexPath *)ip
{
    UITableViewCell *cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleSubtitle
                                                   reuseIdentifier:nil];
    cell.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.06];
    cell.textLabel.textColor = UIColor.whiteColor;
    cell.detailTextLabel.textColor = KSThemeTextSecondary();
    if (self.files.count == 0) {
        cell.textLabel.text = @"对方没有共享文件";
        cell.detailTextLabel.text = @"或对方未允许浏览";
        cell.selectionStyle = UITableViewCellSelectionStyleNone;
        return cell;
    }
    NSDictionary *item = self.files[ip.row];
    BOOL isDir = [item[@"kind"] isEqualToString:@"dir"];
    cell.textLabel.text = item[@"name"] ?: @"—";
    cell.detailTextLabel.text = isDir
        ? @"文件夹"
        : KSFormatBytes((unsigned long long)[item[@"size"] unsignedLongLongValue]);
    cell.imageView.image = [UIImage systemImageNamed:isDir ? @"folder" : @"doc"];
    cell.accessoryType = UITableViewCellAccessoryDisclosureIndicator;
    cell.selectionStyle = UITableViewCellSelectionStyleDefault;
    return cell;
}

- (void)tableView:(UITableView *)tv didSelectRowAtIndexPath:(NSIndexPath *)ip
{
    [tv deselectRowAtIndexPath:ip animated:YES];
    if (ip.row >= (NSInteger)self.files.count) return;
    NSDictionary *item = self.files[ip.row];
    NSString *path = item[@"path"] ?: item[@"name"];
    if ([item[@"kind"] isEqualToString:@"dir"]) {
        KSRemoteFilesViewController *vc = [[KSRemoteFilesViewController alloc] init];
        vc.session = self.session;
        vc.remotePath = path;
        [self.navigationController pushViewController:vc animated:YES];
        return;
    }
    [self.session downloadPath:path];
    [self ks_showError:@"已开始下载" code:@"Get" hint:path];
}

@end

#pragma mark - 全局搜索

@interface KSSearchViewController () <UITableViewDataSource, UITableViewDelegate, UISearchBarDelegate>
@property (nonatomic, strong) UISearchBar *searchBar;
@property (nonatomic, strong) UITableView *table;
@property (nonatomic, strong) NSArray<NSDictionary *> *results;
@end

@implementation KSSearchViewController

- (void)viewDidLoad
{
    [super viewDidLoad];
    self.title = @"搜索";
    self.navigationItem.largeTitleDisplayMode = UINavigationItemLargeTitleDisplayModeNever;

    self.searchBar = [[UISearchBar alloc] init];
    self.searchBar.placeholder = @"搜索文件名 / 传输任务 / 设置项";
    self.searchBar.delegate = self;
    self.searchBar.searchBarStyle = UISearchBarStyleMinimal;
    self.searchBar.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:self.searchBar];

    self.table = [[UITableView alloc] initWithFrame:CGRectZero style:UITableViewStyleInsetGrouped];
    self.table.backgroundColor = UIColor.clearColor;
    self.table.dataSource = self;
    self.table.delegate = self;
    self.table.rowHeight = 58;
    self.table.keyboardDismissMode = UIScrollViewKeyboardDismissModeOnDrag;
    self.table.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:self.table];

    [NSLayoutConstraint activateConstraints:@[
        [self.searchBar.topAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor],
        [self.searchBar.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor constant:12],
        [self.searchBar.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor constant:-12],
        [self.table.topAnchor constraintEqualToAnchor:self.searchBar.bottomAnchor],
        [self.table.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor],
        [self.table.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor],
        [self.table.bottomAnchor constraintEqualToAnchor:self.view.bottomAnchor],
    ]];

    [self buildResults:@""];
}

- (void)viewDidAppear:(BOOL)animated
{
    [super viewDidAppear:animated];
    [self.searchBar becomeFirstResponder];
}

- (NSArray<NSString *> *)settingItems
{
    return @[@"设备名称", @"设备 ID", @"端口", @"发现方式", @"自动连接历史设备",
             @"分块大小", @"限速", @"自动接受", @"断点续传保留时长",
             @"接收目录", @"撤销同意用户协议", @"开发者模式"];
}

- (void)buildResults:(NSString *)keyword
{
    NSString *key = [keyword stringByTrimmingCharactersInSet:
                     [NSCharacterSet whitespaceCharacterSet]].lowercaseString;
    NSMutableArray *out = [NSMutableArray array];

    NSFileManager *fm = [NSFileManager defaultManager];
    NSMutableArray *dirs = [NSMutableArray array];
    if (KSConfig.shared.shareRoot.length) [dirs addObject:KSConfig.shared.shareRoot];
    if (KSConfig.shared.receivePath.length) [dirs addObject:KSConfig.shared.receivePath];
    NSMutableArray *seen = [NSMutableArray array];
    for (NSString *dir in dirs) {
        NSArray *names = [fm contentsOfDirectoryAtPath:dir error:NULL] ?: @[];
        for (NSString *name in names) {
            NSString *full = [dir stringByAppendingPathComponent:name];
            if ([seen containsObject:full]) continue;
            [seen addObject:full];
            if (key.length && ![name.lowercaseString containsString:key]) continue;
            [out addObject:@{@"group": @"文件", @"title": name,
                             @"sub": [NSString stringWithFormat:@"%@ · %@",
                                      KSFormatBytes(KSFileSize(full)),
                                      [dir isEqualToString:KSConfig.shared.receivePath] ? @"已接收" : @"共享目录"],
                             @"path": full}];
        }
    }

    for (KSTransfer *t in [KSTransferCenter shared].transfers) {
        if (key.length && ![t.fileName.lowercaseString containsString:key]) continue;
        [out addObject:@{@"group": @"传输任务", @"title": t.fileName ?: @"—",
                         @"sub": [NSString stringWithFormat:@"%@ · %@", t.stateLabel,
                                  t.direction == KSTransferDirectionUpload ? @"发送" : @"接收"],
                         @"tid": t.transferId ?: @""}];
    }

    for (NSString *item in [self settingItems]) {
        if (key.length && ![item.lowercaseString containsString:key]) continue;
        [out addObject:@{@"group": @"设置", @"title": item, @"sub": @"设置", @"setting": @YES}];
    }

    self.results = out;
    [self.table reloadData];
}

- (void)searchBar:(UISearchBar *)bar textDidChange:(NSString *)text
{
    [self buildResults:text];
}

- (void)searchBarSearchButtonClicked:(UISearchBar *)bar
{
    [bar resignFirstResponder];
}

- (NSInteger)numberOfSectionsInTableView:(UITableView *)tv { return 1; }

- (NSInteger)tableView:(UITableView *)tv numberOfRowsInSection:(NSInteger)section
{
    return MAX(1, (NSInteger)self.results.count);
}

- (NSString *)tableView:(UITableView *)tv titleForHeaderInSection:(NSInteger)section
{
    return self.results.count ? @"搜索结果" : nil;
}

- (UITableViewCell *)tableView:(UITableView *)tv cellForRowAtIndexPath:(NSIndexPath *)ip
{
    static NSString *cid = @"search";
    UITableViewCell *cell = [tv dequeueReusableCellWithIdentifier:cid];
    if (!cell) {
        cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleSubtitle reuseIdentifier:cid];
        cell.backgroundColor = [UIColor colorWithWhite:1.0 alpha:0.06];
        cell.textLabel.textColor = UIColor.whiteColor;
        cell.detailTextLabel.textColor = KSThemeTextSecondary();
    }
    if (self.results.count == 0) {
        cell.textLabel.text = @"没有匹配结果";
        cell.detailTextLabel.text = @"换个关键词试试：文件名、任务名或设置项";
        cell.accessoryType = UITableViewCellAccessoryNone;
        return cell;
    }
    NSDictionary *item = self.results[ip.row];
    cell.textLabel.text = item[@"title"];
    cell.detailTextLabel.text = [NSString stringWithFormat:@"%@ · %@",
                                 item[@"group"], item[@"sub"]];
    cell.accessoryType = UITableViewCellAccessoryDisclosureIndicator;
    return cell;
}

- (void)tableView:(UITableView *)tv didSelectRowAtIndexPath:(NSIndexPath *)ip
{
    [tv deselectRowAtIndexPath:ip animated:YES];
    if (ip.row >= (NSInteger)self.results.count) return;
    NSDictionary *item = self.results[ip.row];
    if (item[@"tid"]) {
        KSTransfer *t = [[KSTransferCenter shared] transferWithId:item[@"tid"]];
        if (!t) return;
        KSTransferDetailViewController *vc = [[KSTransferDetailViewController alloc] init];
        vc.transfer = t;
        [self.navigationController pushViewController:vc animated:YES];
    } else if (item[@"setting"]) {
        [self.navigationController pushViewController:[[KSSettingsViewController alloc] init]
                                            animated:YES];
    } else if (item[@"path"]) {
        NSString *path = item[@"path"];
        UIActivityViewController *av = [[UIActivityViewController alloc]
            initWithActivityItems:@[[NSURL fileURLWithPath:path]] applicationActivities:nil];
        [self presentViewController:av animated:YES completion:nil];
    }
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
    if (s == 3) return 5 + (KSConfig.shared.developerUnlocked ? 1 : 0);
    return [@[@2, @4, @2][s] integerValue];
}

- (void)viewWillAppear:(BOOL)animated
{
    [super viewWillAppear:animated];
    [self.table reloadData];
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
    if (ip.row == 3) {
        UITableViewCell *cell = [self cellWithTitle:@"用户协议与隐私政策"
                                             detail:(KSConfig.shared.legalAccepted ? @"已同意" : @"未同意")
                                           switchOn:NO];
        cell.textLabel.textColor = KSTheme.shared.accent;
        return cell;
    }
    if (ip.row == 4) return [self cellWithTitle:@"配置文件" detail:@"settings.json" switchOn:NO];
    return [self cellWithTitle:@"开发者选项" detail:@"" switchOn:NO];
}

- (void)onKernelLongPress:(UILongPressGestureRecognizer *)g
{
    if (g.state != UIGestureRecognizerStateBegan) return;
    if (KSConfig.shared.developerLocked) return;    // 已永久锁定
    if (KSConfig.shared.devGateMode != 0) return;   // 密码输错后主入口失效
    UIImpactFeedbackGenerator *h = [[UIImpactFeedbackGenerator alloc] initWithStyle:UIImpactFeedbackStyleMedium];
    [h impactOccurred];
    [self promptDeveloperPassword];
}
- (NSString *)nextErrorCode
{
    // 5 位大写十六进制，来自时间戳与设备 ID 的摘要，便于对日志排查
    static NSUInteger counter = 0;
    counter++;
    NSString *seed = [NSString stringWithFormat:@"%@-%lu-%f",
                      KSConfig.shared.deviceId ?: @"", (unsigned long)counter,
                      [NSDate date].timeIntervalSince1970];
    NSString *digest = KSSHA256HexOfData([seed dataUsingEncoding:NSUTF8StringEncoding]);
    return [[digest substringToIndex:5] uppercaseString];
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
        UINotificationFeedbackGenerator *bad = [[UINotificationFeedbackGenerator alloc] init];
        [bad notificationOccurred:UINotificationFeedbackTypeError];
        if (KSConfig.shared.devGateMode == 1) {
            // 备用入口再次输错：显示错误提示与错误码
            NSString *code = [weakSelf nextErrorCode];
            [[KSLog shared] warn:@"开发者入口校验失败（备用入口），错误码 ERROR-%@", code];
            UIAlertController *err = [UIAlertController
                alertControllerWithTitle:nil
                                 message:[NSString stringWithFormat:@"错误！详细信息：\n ERROR-%@", code]
                          preferredStyle:UIAlertControllerStyleAlert];
            [err addAction:[UIAlertAction actionWithTitle:@"好" style:UIAlertActionStyleDefault handler:nil]];
            [weakSelf presentViewController:err animated:YES completion:nil];
            KSConfig.shared.developerLocked = YES;
            [KSConfig.shared save];
            return;
        }
        // 主入口密码错误：入口切换为「连点版本号 16 次」，且不提示任何信息
        KSConfig.shared.devGateMode = 1;
        [KSConfig.shared save];
        [[KSLog shared] warn:@"开发者入口校验失败，已切换到备用入口"];
    }]];
    [al addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
    [self presentViewController:al animated:YES completion:nil];
}
- (void)revokeLegal
{
    UIAlertController *al = [UIAlertController
        alertControllerWithTitle:@"撤销同意"
                         message:@"撤销后需要重新阅读并同意协议与隐私政策才能继续使用。"
                  preferredStyle:UIAlertControllerStyleAlert];
    [al addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
    __weak typeof(self) weakSelf = self;
    [al addAction:[UIAlertAction actionWithTitle:@"撤销" style:UIAlertActionStyleDestructive
                                          handler:^(UIAlertAction *a) {
        KSConfig.shared.legalAccepted = NO;
        [KSConfig.shared save];
        KSLegalPresentIfNeeded(weakSelf, ^{
            [[KSLog shared] info:@"已重新同意协议"];
            [[KSServer shared] start];
            [weakSelf.table reloadData];
        });
    }]];
    [self presentViewController:al animated:YES completion:nil];
}
- (void)tapVersion
{
    // 主入口模式下，连点版本号不做任何事、不提示，避免暴露入口
    if (KSConfig.shared.developerLocked) {
        self.tapCount = 0;
        return;                          // 永久锁定后连点无反应
    }
    if (KSConfig.shared.devGateMode != 1) {
        self.tapCount = 0;
        return;
    }
    self.tapCount++;
    [self.tapTimer invalidate];
    self.tapTimer = [NSTimer scheduledTimerWithTimeInterval:3.0 target:self
                                                   selector:@selector(resetTaps) userInfo:nil repeats:NO];
    if (self.tapCount >= 16) {
        self.tapCount = 0;
        [self promptDeveloperPassword];
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
    if (ip.section == 3 && ip.row == 3) { [self showLegalSheet]; return; }
    if (ip.section == 3 && ip.row == 4) { [self showConfig]; return; }
    if (ip.section == 3 && ip.row == 5 && KSConfig.shared.developerUnlocked) {
        KSDeveloperViewController *dev = [[KSDeveloperViewController alloc] init];
        [self.navigationController pushViewController:dev animated:YES];
        return;
    }
}

- (void)showLegalSheet
{
    UIAlertController *sheet = [UIAlertController
        alertControllerWithTitle:@"用户协议与隐私政策"
                         message:@"撤销同意后会立刻回到协议页，未同意不能继续使用。"
                  preferredStyle:UIAlertControllerStyleActionSheet];
    [sheet addAction:[UIAlertAction actionWithTitle:@"查看全文" style:UIAlertActionStyleDefault
                                           handler:^(UIAlertAction *a) {
        // 已同意时 KSLegalPresentIfNeeded 会直接返回，这里强制打开只读全文
        UIViewController *legal = KSLegalMakeViewController(^(BOOL accepted) {
            [self dismissViewControllerAnimated:YES completion:nil];
            [self.table reloadData];
        });
        legal.modalPresentationStyle = UIModalPresentationFullScreen;
        [self presentViewController:legal animated:YES completion:nil];
    }]];
    [sheet addAction:[UIAlertAction actionWithTitle:@"撤销同意" style:UIAlertActionStyleDestructive
                                           handler:^(UIAlertAction *a) {
        [self revokeLegal];
    }]];
    [sheet addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
    sheet.popoverPresentationController.sourceView = self.table;
    [self presentViewController:sheet animated:YES completion:nil];
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




#pragma mark - 底部微弹窗

@implementation UIViewController (KSErrorToast)

- (void)ks_showError:(NSString *)message code:(NSString *)code hint:(NSString *)hint
{
    NSString *title = @"错误！！！";
    NSString *body = [NSString stringWithFormat:@"错误信息：\n ERROR-%@", code ?: @"Unknown"];
    if (hint.length) {
        body = [NSString stringWithFormat:@"%@\n常见错误环境：%@", body, hint];
    }

    UIWindow *window = self.view.window ?: UIApplication.sharedApplication.keyWindow;
    if (!window) return;

    UIView *card = [[UIView alloc] init];
    card.backgroundColor = [UIColor colorWithRed:0.11 green:0.12 blue:0.16 alpha:0.97];
    card.layer.cornerRadius = 18;
    card.layer.cornerCurve = kCACornerCurveContinuous;
    card.translatesAutoresizingMaskIntoConstraints = NO;
    card.tag = 88001;
    card.alpha = 0;

    UILabel *titleLabel = [[UILabel alloc] init];
    titleLabel.text = title;
    titleLabel.font = [UIFont systemFontOfSize:15 weight:UIFontWeightBold];
    titleLabel.textColor = [UIColor colorWithRed:1 green:0.42 blue:0.42 alpha:1];
    titleLabel.translatesAutoresizingMaskIntoConstraints = NO;

    UILabel *bodyLabel = [[UILabel alloc] init];
    bodyLabel.text = body;
    bodyLabel.numberOfLines = 0;
    bodyLabel.font = [UIFont systemFontOfSize:12.5];
    bodyLabel.textColor = [UIColor colorWithWhite:1 alpha:0.88];
    bodyLabel.translatesAutoresizingMaskIntoConstraints = NO;

    [card addSubview:titleLabel];
    [card addSubview:bodyLabel];
    [window addSubview:card];

    [NSLayoutConstraint activateConstraints:@[
        [card.leadingAnchor constraintEqualToAnchor:window.leadingAnchor constant:12],
        [card.trailingAnchor constraintEqualToAnchor:window.trailingAnchor constant:-12],
        [card.bottomAnchor constraintEqualToAnchor:window.safeAreaLayoutGuide.bottomAnchor constant:-10],
        [titleLabel.topAnchor constraintEqualToAnchor:card.topAnchor constant:12],
        [titleLabel.leadingAnchor constraintEqualToAnchor:card.leadingAnchor constant:16],
        [bodyLabel.topAnchor constraintEqualToAnchor:titleLabel.bottomAnchor constant:6],
        [bodyLabel.leadingAnchor constraintEqualToAnchor:card.leadingAnchor constant:16],
        [bodyLabel.trailingAnchor constraintEqualToAnchor:card.trailingAnchor constant:-16],
        [bodyLabel.bottomAnchor constraintEqualToAnchor:card.bottomAnchor constant:-12]
    ]];

    card.transform = CGAffineTransformMakeTranslation(0, 140);
    [UIView animateWithDuration:0.32 delay:0 usingSpringWithDamping:0.86 initialSpringVelocity:0.4
                        options:UIViewAnimationOptionCurveEaseOut animations:^{
        card.alpha = 1;
        card.transform = CGAffineTransformIdentity;
    } completion:nil];

    __weak UIView *weakCard = card;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(5.0 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        [UIView animateWithDuration:0.25 animations:^{
            weakCard.alpha = 0;
            weakCard.transform = CGAffineTransformMakeTranslation(0, 120);
        } completion:^(BOOL done) {
            [weakCard removeFromSuperview];
        }];
    });
    [[KSLog shared] warn:@"%@ (ERROR-%@)", message, code];
}

@end
