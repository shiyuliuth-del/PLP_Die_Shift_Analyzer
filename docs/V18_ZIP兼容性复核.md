# V18源码ZIP兼容性复核

2026-10-09。归档修订V18-zip1，应用和算法版本仍为V18。

用户反馈：从聊天链接下载后，7-Zip提示某个PNG数据错误。本机下载目录中的旧ZIP与GitHub重新下载的旧ZIP均为19,538,640字节，SHA256均为 `83c850f8b9ac5ac458016036664136703b89495614f23b5c4294bdaa819eba38`；两者在本机通过7-Zip/unzip测试和完整解压，64张PNG通过内部校验和像素解码。尚未取得报错副本的校验值及具体PNG名称，未确定原报错原因，不能据此认定图片本身损坏。

现提供 `Die_Shift_Analyze_V18_Compatible.zip`，采用ZIP Store（不使用Deflate），包含显式目录、普通文件类型、标准属性和UTF-8文件名，无加密、无ZIP64。根目录缩短为 `Die_Shift_Analyze_V18`。315个文件的数据与原V18包逐字节一致。

- 文件大小：31,093,892字节。
- SHA256：`6db4b4306fc2490cac0f7ee85dd96d168b77bc990d01b6f0b12975a03ad4becd`。
- 独立解压：7-Zip、unzip，各315个文件哈希一致；各64张PNG校验及完整像素解码通过。
- 从7-Zip解压后的目录启动源码冒烟检查通过。
- 验证环境：当前Linux环境；未声称完成Windows实机验证。

[GitHub V18下载页](https://github.com/shiyuliuth-del/PLP_Die_Shift_Analyzer/releases/tag/V18)中的Compatible文件是本次重打包结果。旁边的 `.sha256` 文件用于核对下载完整性。详细记录见[归档验证JSON](../results/zip_compatibility_V18.json)。

复现方式：

```bash
python3 tools/repack_compatible_zip.py dist/Die_Shift_Analyze_V18_Source.zip dist/Die_Shift_Analyze_V18_Compatible.zip
7z t dist/Die_Shift_Analyze_V18_Compatible.zip
```

新压缩包只改变归档方式和文件属性，不替换软件功能或改动图像数据。归档工具保存在本地Git及仓库中，供后续版本需要兼容包时复用。
