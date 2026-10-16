#!/usr/bin/env bash
set -Eeuo pipefail

# ==================== 只需修改这一段 ====================
SUBJECT="003"                 # 不要写 sub- 前缀
SESSIONS=()                   # 全部 session: (); 指定: ("01"); 多个: ("01" "02")

PROJECT_DIR="/home/cyliu/lcy_data"
BIDS_DIR="/mnt/f/fMRIprep_prep_code/bids"
OUTPUT_DIR="${PROJECT_DIR}/derivatives_surface"
WORK_DIR="${PROJECT_DIR}/work_surface"
FS_LICENSE="${PROJECT_DIR}/license.txt"

# 固定版本以保证可复现；需要升级时明确修改版本并重新验证。
FMRIPREP_IMAGE="nipreps/fmriprep:25.2.5"
NPROCS=12
OMP_NTHREADS=8
MEM_MB=60000

# 多次扫描且每个 session 有 T1w：
# sessionwise = 每个 session 使用各自的 T1w；unbiased = 跨 session 构建无偏参考。
ANAT_REFERENCE="unbiased"

# 没有可用的 fieldmap/反向相位编码 EPI 时才启用。
USE_SYN_SDC=false

# true 会在成功或失败后清理工作目录，不利于断点续跑；通常保持 false。
CLEAN_WORKDIR=false
# ========================================================

die() { printf '错误: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "未找到 docker"
[[ -d "$BIDS_DIR" ]] || die "BIDS 目录不存在: $BIDS_DIR"
[[ -f "$BIDS_DIR/dataset_description.json" ]] || \
  die "缺少 $BIDS_DIR/dataset_description.json；输入目录可能还不是完整 BIDS 数据集"
[[ -d "$BIDS_DIR/sub-$SUBJECT" ]] || die "缺少被试目录: $BIDS_DIR/sub-$SUBJECT"
[[ -s "$FS_LICENSE" ]] || die "FreeSurfer license 不存在或为空: $FS_LICENSE"

mkdir -p "$OUTPUT_DIR" "$WORK_DIR"

args=(
  /data /out participant
  --participant-label "$SUBJECT"
  --fs-license-file /opt/freesurfer/license.txt
  --work-dir /work
  --output-spaces T1w MNI152NLin2009cAsym:res-2 fsnative fsaverage5
  --cifti-output 91k
  --subject-anatomical-reference "$ANAT_REFERENCE"
  --no-track-sessions
  --bold2anat-init auto
  --nprocs "$NPROCS"
  --omp-nthreads "$OMP_NTHREADS"
  --mem-mb "$MEM_MB"
  --stop-on-first-crash
)

if ((${#SESSIONS[@]})); then
  args+=(--session-label "${SESSIONS[@]}")
fi
[[ "$USE_SYN_SDC" == true ]] && args+=(--use-syn-sdc warn)
[[ "$CLEAN_WORKDIR" == true ]] && args+=(--clean-workdir)

printf '被试: sub-%s\n镜像: %s\n输出: %s\n' "$SUBJECT" "$FMRIPREP_IMAGE" "$OUTPUT_DIR"
if ((${#SESSIONS[@]})); then
  printf 'Session: %s\n' "${SESSIONS[*]}"
else
  printf 'Session: 全部\n'
fi

docker run --rm -it \
  -v "$BIDS_DIR:/data:ro" \
  -v "$OUTPUT_DIR:/out" \
  -v "$WORK_DIR:/work" \
  -v "$FS_LICENSE:/opt/freesurfer/license.txt:ro" \
  "$FMRIPREP_IMAGE" \
  "${args[@]}"
