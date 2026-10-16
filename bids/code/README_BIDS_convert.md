# DICOM → BIDS 转换操作速查

本目录完成工作流的转换阶段。架构、完整流程、预处理分支和结果说明见
[项目 README](../../README.md)。

## 文件职责

| 文件 | 作用 |
| --- | --- |
| `convert_date_to_bids.py` | 识别日期、被试、session 和 run，调用 dcm2niix，整理 BIDS 文件和元数据 |
| `run_bids_convert.bat` | Windows 入口：优先启动 `py -3`，不可用时使用 `python`，转交参数和退出码 |
| `README_BIDS_convert.md` | 转换阶段的操作速查 |

需要 Python 3.10+，仅使用标准库；实际转换另需 dcm2niix。
**程序不读取 Excel 内容，也不从 Excel 生成事件表。** 请提前准备对应的 `events.tsv`。

## 输入约定

```text
fMRI_raw/YYYYMMDD/<含 sub001 等编号的目录>/<序列目录>/DICOM
behavior/sub-001/sub-001_ses-02_task-tactile_run-05_YYYYMMDD_HHMMSS.xlsx
behavior/sub-001/sub-001_ses-02_task-tactile_run-05_events.tsv
```

任务序列目录以 `bold` 开头且含 `taskN`，静息序列以 `bold` 开头且含 `rest`。
结构像以 `t1_mprage` 开头；三件场图以 `fieldmap` 开头，分别含 `echo1`、`echo2`、`b0map`。
完整分类和事件查找规则见主 README 第 3 节。

同一被试同一天，任务按 `taskN` 数值排序，与行为记录按 run 数值排序后逐项配对。
两者数量必须一致，行为记录只属于一个 session。程序不通过时间或试次内容验证配对。

## 先预览，再转换

在 PowerShell 中进入**项目根目录**，修改示例日期后执行：

```powershell
$repo = (Get-Location).Path
$convertArgs = @(
  '--raw-root', (Join-Path $repo 'fMRI_raw'),
  '--behavior-root', (Join-Path $repo 'behavior'),
  '--bids-root', (Join-Path $repo 'bids'),
  '--date', '20260708'
)

& "$repo\bids\code\run_bids_convert.bat" @convertArgs --dry-run
```

确认被试、session、`taskN->run-XX` 和警告后，在同一窗口运行：

```powershell
& "$repo\bids\code\run_bids_convert.bat" @convertArgs
```

不用 `.bat` 时也可直接调用：

```powershell
python "$repo\bids\code\convert_date_to_bids.py" @convertArgs --dry-run
```

显式指定三个根目录，可以避免误用内置的 `H:\lcy_data\...` 默认位置。

## 参数

| 参数 | 作用 |
| --- | --- |
| `--raw-root PATH` | DICOM 根目录，默认 `H:\lcy_data\fMRI_raw` |
| `--behavior-root PATH` | 行为根目录，默认 `H:\lcy_data\behavior` |
| `--bids-root PATH` | 输出根目录，默认 `H:\lcy_data\bids` |
| `--date 20260703 20260708` | 一个或多个日期；不传日期且不用 `--all` 时选择最新日期目录 |
| `--all` | 全部日期，不能与 `--date` 同用 |
| `--dcm2niix PATH` | 可执行文件位置，默认从 `PATH` 查找 `dcm2niix` |
| `--dry-run` | 只打印计划，不运行 dcm2niix，不写入 |
| `--overwrite` | 重新生成目标影像/JSON，覆盖事件表 |
| `--help` | 查看帮助 |

`$convertArgs` 已含 `--date`，改用 `--all` 时先移除其中的 `--date` 和日期值。
指定 dcm2niix 时，可在调用后追加 `--dcm2niix 'D:\tools\dcm2niix\dcm2niix.exe'`。

## 输出与重跑行为

- `anat/*_T1w.nii.gz` 及 JSON。
- `func/*_task-tactile_run-*_bold.nii.gz`、JSON 和复制的事件 TSV。
- 可选的静息态 BOLD、JSON 和只有表头的事件 TSV。
- 三件场图齐备时生成 `magnitude1`、`magnitude2`、`phasediff`，补充关联与可获取的 EchoTime。
- 缺失时创建数据集描述，向被试表追加尚未登记的被试。

默认跳过完整存在的影像/JSON 对和已有事件表，但仍可能更新 JSON 的 `TaskName`、
`IntendedFor`、EchoTime，或移除指定的非 BOLD 读出字段。影像/JSON 只有一份时会报错。
`--overwrite` 会替换目标文件，使用前检查日期和输出位置。

没有 T1w 或场图不齐时会警告；场图不齐时本次整组场图跳过。
没有行为 run 的数据默认进入 `ses-01`。每个输入序列必须恰好产生一份 NIfTI 和一份 JSON。

## 转换之后

检查事件对应、影像元数据和场图关联，使用 BIDS Validator 校验输出，再按主 README 将完整
BIDS 数据集交给 Linux/WSL2 的 fMRIPrep 入口。预览或日志中的 `Done.` 不能代替校验和预处理。
