# Die Shift Analyze 与 RealDataAnalyse 离线签发

## 首次部署

1. 在 Windows 解压 Release 源码包。在已安装依赖的 Python 环境中双击根目录的 `LicenseManager_BUILD.bat`。签发工具可以先独立构建，无需公钥。完成后打开 `dist_admin\LicenseManager.exe`。
2. 点「生成密钥对」，默认保存在签发工具同目录的 `keys` 子目录并设置私钥密码。工具生成加密私钥和 `public_key.pem`，并在签发工具目录生成管理员通用许可 `Universal.lic`。
3. 双击根目录的 `BUILD.bat`，构建两个用户程序。脚本会自动读取 `public_key.pem`（优先环境变量 `DIE_SHIFT_LICENSE_PUBLIC_KEY`，其次源码根目录、同仓 `dist_admin/keys`、`dist_admin` 或原默认密钥目录）。
4. 用户把管理员签发的 `.lic` 放到两个 EXE 所在的同一个目录。两个程序通过同一公钥验证，启动时自动读取这份许可。若授权窗口已打开，放入文件后点击「重新检查」。

保留加密的私钥及其备份。发布时只需要内置公钥。通用许可可授权任何能够生成 Machine ID 的电脑，仅保存在管理员目录。首次生成时如果工具目录不可写，可以把工具移到管理员可写的目录，再用已有私钥打开「首次部署」入口。

## 日常签发

加载加密私钥，粘贴用户提供的一行 Machine ID，点击「签发许可证」。沿用 V80 已验证的 Windows Machine ID。签发历史支持按授权编号或 Machine ID 检索、重新导出、准备重签、导出 CSV 和备份。

签发工具的默认工作/历史数据目录为 `LicenseManager.exe` 同目录，默认签发输出目录为同目录下的 `licenses` 子目录，默认密钥目录为同目录下的 `keys` 子目录。也可在源码根目录运行 `python -m LicenseManager.main` 直接打开工具。

## 离线构建

在与目标电脑的 Python 版本和架构相同的联网 Windows 环境中执行 `python -m pip wheel -r requirements.txt -w wheels`。把 wheel 文件复制到离线电脑，通过 `python -m pip install --no-index --find-links wheels -r requirements.txt` 安装后运行构建脚本。也可设置 `DIE_SHIFT_PYTHON` 指向已安装依赖的解释器。运行已构建的 EXE 无需另装 Python。
