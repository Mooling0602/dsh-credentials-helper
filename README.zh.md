# dsh-credentials-helper

[English](README.md) | 简体中文

一个 [dsh-mloader](https://github.com/Mooling0602/dsh-mloader) Python 插件，用一个面向用户的斜杠命令操作 DeepSeek Harness 的凭据引用。插件不注册任何工具，因此模型无法通过这个插件读写凭据文档。

插件作用于 `<DSH_HOME>/.credentials.yaml`，也就是 Harness 凭据提供方拥有的文件。它读取 `refs` 映射，可以替换一个值或新增一个条目。写入需要经过确认步骤，文件始终保持仅属主可读写，文档在 Harness 使用的同一把写入锁下以原子方式替换。对于没有 `refs` 段、或只剩写入器删除最后一个引用后留下的空内联形态的文档，第一次写入会补出一个块映射。

[![dsh-mloader 徽章](https://img.shields.io/badge/powered_by-dsh--mloader-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/Mooling0602/dsh-mloader)
[![MIT 协议徽章](https://img.shields.io/github/license/Mooling0602/dsh-credentials-helper?style=flat-square)](https://github.com/Mooling0602/dsh-credentials-helper/blob/main/LICENSE)

## 要求

- 已安装 dsh-mloader 的 dsh。
- Python 后端，它创建共享的 profile 环境。
- Python 3.10 或更高版本，用于插件代码。

## 安装

插件目录是本仓库的 `src/` 树。在 dsh 运行期间把它复制到 profile 插件根目录，并以插件 id 命名副本：

```sh
mkdir -p <DSH_HOME>/profiles/<profile>/mloader/plugins
cp -r src <DSH_HOME>/profiles/<profile>/mloader/plugins/dsh-credentials-helper
```

加载器监听插件根目录并挂载该目录。监听关闭时，用 `/mloader load dsh-credentials-helper` 加载。命令请求 `/cred` 这个名字，若它已被其他插件占用则退回 `/credentials`。若要以包的形式安装，从同一目录构建归档文件，交给运行时命令行或加载器的安装命令：

```sh
python3 <dsh-mloader>/src/runtime/python_sdk/run_cli.py pack src -o dist/dsh-credentials-helper.mloader.pack
/mloader install dist/dsh-credentials-helper.mloader.pack
```

## 命令

| 命令 | 作用 |
| --- | --- |
| `/cred set <KEY> <VALUE>` | 暂存对引用 `KEY` 的更新并请求确认。 |
| `/cred set <KEY> <VALUE> --skip-confirm` | 立即写入更新。 |
| `/cred set <KEY> -- <VALUE>` | 暂存一个以 `--` 开头的值。 |
| `/cred read <KEY>` | 打印 `KEY` 解码后的值。 |
| `/cred ok` 或 `/cred confirm` | 写入正在等待确认的更新。 |
| `/cred help` | 显示用法文本。 |

`KEY` 是凭据引用名，即 Harness 在 `refs` 里使用的环境变量风格名称（`[A-Za-z_][A-Za-z0-9_]*`）。`VALUE` 是一个参数：包含空格的值需要加引号，以两个短横线开头的值要在它前面加 `--`。新的 `KEY` 会加入 `refs` 映射；已存在的键保持原有位置。

## 确认

第一次 `/cred set` 把待处理更新保存在插件进程中，并显示引用名以及掩码后的当前值和新值。此时不会写入任何内容。满足下列条件之一时才写入：

- 操作者执行 `/cred ok` 或 `/cred confirm`；
- 操作者再次执行完全相同的 `/cred set` 命令；
- 原命令带有 `--skip-confirm`。

另一个 `/cred set` 会替换待处理更新。文档尚不存在时，确认信息把当前值显示为 `(not set)`，暂存写入会按 version 1 布局创建该文档。待处理值只存在于插件进程内存中，插件卸载时丢弃。

## 掩码与输出

除 `/cred read` 外的所有消息都显示掩码值：十二个字符及以下完全隐藏；更长的值只显示三个或四个字符的前缀和后缀，且可见部分不超过整个值的一半。`/cred read` 打印解码后的值，因为这就是它的用途。错误消息从不包含解码后的值；解析错误只报告文档路径和行号，从不报告出错的内容。

## 文档位置

Harness 把文档保存在 `<DSH_HOME>/.credentials.yaml`，其中 `DSH_HOME` 默认为 `~/.dsh`。加载器启动插件子进程时不带任何 `DSH_*` 变量，因此插件按以下顺序解析 home：

1. 插件环境里显式的 `DSH_HOME` 值，该值必须是绝对路径；
2. 安装布局，即插件从 `<home>/profiles/<profile>/mloader/plugins/dsh-credentials-helper` 运行的情况；
3. `~/.dsh`。

第一种和最后一种覆盖默认安装。第二种让自定义 `DSH_HOME` 继续可用，因为插件可以从自身位置推导出同一个 home。

## 写入安全

存储会获取 Harness 使用的同一把跨进程写入锁（`<文档>.lock`：以 `wx` 语义创建、记录持有者进程 id、写入周期结束时删除），整个读-改-写过程都在锁内完成，因此插件与 Harness 不会互相覆盖对方的快照。记录中的进程已退出的锁会通过认领文件接管；等待超时后命令直接失败，而不是覆盖一个插件没能读到的状态。

标量读取覆盖 Harness 写入器会产生的形态：plain、单引号、双引号标量（含跨行折行和双引号转义），以及带全部 chomping 指示符的 literal 与 folded 块标量。子集之外的写法会被拒绝而不是猜测。`refs` 段可以是块映射、写入器删除最后一个引用后留下的空内联形态（`refs: {}`）或缺失；对缺失段执行写入会创建 version 1 布局，且只针对已声明该版本、或除注释外为空的文档。注释和空行不会截断该段，已存在条目的缩进会被沿用。YAML 不允许用 tab 作缩进，因此 tab 缩进的条目会得到明确错误而不是被改写。

只有目标条目会被替换。随后存储会校验 refs 映射以及该段前后每个字节都未改变（除那一个值之外）；若有不遵守锁的写入方在此期间改了文件，指纹检查会拒绝这次更新。新文本写到文档旁边一个仅属主可读写的临时文件，刷新到磁盘后再重命名覆盖文档，因此失败不会留下写了一半的凭据文件，目录也会在平台支持时刷新。Windows 上会重试瞬时的替换失败，并跳过仅属主 chmod 步骤，因为 Windows 没有描述符级的权限位。注释、文档版本、`records` 段以及其余每个条目的格式都保留。

## 安全说明

- 插件注册的是命令而非工具。命令执行不经过模型，处理函数使用插件自身的文件访问权限，因此这个插件只是在 Python 插件本就拥有的访问能力之上增加了一个面向人的入口。
- dsh 会把命令生命周期条目记录到会话事件日志。`/cred set` 那一行包含作为命令参数的新值，`/cred read` 的结果包含它打印的值。命令不会发送给模型，但日志会持久化。请像对待凭据文件一样谨慎对待会话存储，如果该存储可能暴露，就轮换对应的值。
- 确认步骤防止误写。它不是认证；写入锁是协调机制而非安全边界：以同一用户身份运行的任何本地进程都可以直接读写该文档。
- 存储以 600 权限写入。Harness 凭据提供方拒绝提供属主之外可读的文档。

## 开发

仓库根目录是一个 uv 项目，包位于 `src/` 下，因此一次同步就能安装插件包和工具。开发依赖组从 SDK 仓库拉取 Python SDK；加载器在运行时总会提供 SDK，所以插件不在运行时依赖中声明 SDK。

```sh
uv sync
uv run python -m unittest discover -s tests -v
./check_code.sh
./format_code.sh --apply
```

`check_code.sh` 运行 ty、ruff check、ruff format 检查以及文档字符串/代码格式检查。`format_code.sh` 只检查格式而不写入，`--apply` 会重写文件。两个脚本都使用 uv 环境里的工具，找不到时回退到 `PATH`。测试里包含一份由 Harness 写入器生成的文档，因此写入器产生的标量形态和 refs 段形态始终被覆盖。

插件没有第三方运行时依赖。它自己解析 refs 子集，因此共享的 profile 环境只需要解释器。
