# Die Shift Analyze 管理员离线签发工具

## 首次部署

在 Windows 构建电脑已安装根目录 `requirements.txt` 的环境中运行 `BUILD.bat --admin-only`。将 `dist_admin/LicenseManager.exe` 单独交给管理员，可在未安装 Python 的 Windows 10/11 电脑上离线运行。管理员目录不随用户端部署。

在 License Manager 中选择「生成密钥对」，选择独立目录并设置私钥密码。请保留加密的 `private_key.pem` 和备份，仅将 `public_key.pem` 交给构建人员。工具会在自身目录一次创建 `Universal.lic`。同一公钥的后续正式版本可反复导入此文件，使用者无需按机器重新签发。通用许可可激活任何能够读取有效硬件信息的电脑，须只保存在管理员目录。如果工具目录不可写，将程序放到管理员有写权限的目录后，用已生成的私钥打开「首次部署」功能。

设置 `DIE_SHIFT_LICENSE_PUBLIC_KEY` 指向该公钥，或把公钥放到源码根目录并命名为 `public_key.pem`，运行 `BUILD.bat`。程序在 `dist`，管理员工具在 `dist_admin`。用户端只部署两个用户软件及其启动器。正式构建缺少公钥时会停止。后续版本使用同一个公钥。加密私钥、通用许可、授权历史和测试密钥都保留在发布目录之外。

## 日常签发

用户提供一行 64 位 Machine ID。加载加密私钥后，输入姓名、工号和 Machine ID，点击「签发许可证」。只需把该用户的 `.lic` 文件发给用户。授权历史可搜索、重导出原文件、准备重签发、导出 CSV 或备份本地数据库。日期仅用于查阅授权记录。

默认数据目录为 `%LOCALAPPDATA%\DieShiftLicenseManager`，可在界面修改。密码只用于生成或解锁私钥。开发环境可在源码根目录运行 `python -m LicenseManager.main`。

## 离线构建

用与目标 Windows Python 版本和架构一致的联网构建环境，执行 `python -m pip wheel -r requirements.txt -w wheels`。把 wheel 文件复制到离线构建电脑，通过 `python -m pip install --no-index --find-links wheels -r requirements.txt` 安装后运行构建脚本。运行已构建的 EXE 无需另装 Python。

## 保护边界

这是面向普通复制行为的 Level 1 离线保护。PyInstaller 提供程序封装，Ed25519 验证许可证。硬件标识可能被仿造，客户端可以被逆向改动。授权绑定电脑，永久许可证无法远程撤销。通用许可证外流会失去逐机绑定的保护效果。
