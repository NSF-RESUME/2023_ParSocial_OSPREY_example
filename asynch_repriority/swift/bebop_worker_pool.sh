#! /usr/bin/env bash

set -eu

if [ "$#" -ne 2 ]; then
  script_name=$(basename $0)
  echo "Usage: ${script_name} exp_id cfg_file"
  exit 1
fi

export TURBINE_LOG=0 TURBINE_DEBUG=0 ADLB_DEBUG=0
# export TURBINE_STDOUT=out-%%r.txt
export TURBINE_STDOUT=
export ADLB_TRACE=0
export EMEWS_PROJECT_ROOT=$( cd $( dirname $0 )/.. ; /bin/pwd )
# source some utility functions used by EMEWS in this script                                                                                 
# source "${EMEWS_PROJECT_ROOT}/swift-t/ext/emews_utils.sh"

export EXPID=$1
export TURBINE_OUTPUT=$EMEWS_PROJECT_ROOT/experiments/$EXPID
# check_directory_exists

CFG_FILE=$2
source $CFG_FILE

echo "--------------------------"
# echo "WALLTIME:              $CFG_WALLTIME"
echo "PROCS:                 $CFG_PROCS"
echo "PPN:                   $CFG_PPN"
echo "DB_HOST:               $CFG_DB_HOST"
echo "DB_USER:               $CFG_DB_USER"
echo "TASK_TYPE:             $CFG_TASK_TYPE"
echo "--------------------------"

export PROJECT=$CFG_PROJECT
export QUEUE=$CFG_QUEUE
export WALLTIME=$CFG_WALLTIME

export PROCS=$CFG_PROCS
export PPN=$CFG_PPN
export TURBINE_JOBNAME="${EXPID}_job"

export TURBINE_RESIDENT_WORK_WORKERS=1
export RESIDENT_WORK_RANK=$(( PROCS - 2 ))


export DB_HOST=$CFG_DB_HOST
export DB_USER=$CFG_DB_USER
export DB_PORT=$CFG_DB_PORT
export DB_NAME=$CFG_DB_NAME

# if R cannot be found, then these will need to be
# uncommented and set correctly.
# export R_HOME=/path/to/R
#export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:$R_HOME/lib
# if python packages can't be found, then uncommited and set this
# PYTHONPATH="/lcrc/project/EMEWS/bebop/repos/probabilistic-sensitivity-analysis:"
# PYTHONPATH+="/lcrc/project/EMEWS/bebop/repos/panmodel-0.20.0:"
# PYTHONPATH+="$EMEWS_PROJECT_ROOT/python"
# export PYTHONPATH
# echo "PYTHONPATH: $PYTHONPATH"

# swift extension locations
EMEWS_EXT=$EMEWS_PROJECT_ROOT/swift/ext

MACHINE="slurm"

if [ -n "$MACHINE" ]; then
  MACHINE="-m $MACHINE"
fi

mkdir -p $TURBINE_OUTPUT
cp $CFG_FILE $TURBINE_OUTPUT/cfg.cfg

CFG_EXTRA_FILES_TO_INCLUDE=${CFG_EXTRA_FILES_TO_INCLUDE:-}
for f in ${CFG_EXTRA_FILES_TO_INCLUDE[@]}; do
  tf="$(basename -- $f)"
  cp $EMEWS_PROJECT_ROOT/$f $TURBINE_OUTPUT/$tf
done

CMD_LINE_ARGS="--sim_work_type=$CFG_TASK_TYPE --batch_size=$CFG_BATCH_SIZE "
CMD_LINE_ARGS+="--batch_threshold=$CFG_BATCH_THRESHOLD --worker_pool_id=$CFG_POOL_ID $*"


# Add any script variables that you want to log as
# part of the experiment meta data to the USER_VARS array,
# for example, USER_VARS=("VAR_1" "VAR_2")
USER_VARS=("MODEL_DIR" "STOP_AT" "MODEL_PROPS" \
 "STOP_AT")
# log variables and script to to TURBINE_OUTPUT directory

# PG_LIB=/usr/lib/postgresql/12/lib
# log_script

# echo's anything following this standard out
# set -x

export TURBINE_LAUNCHER=srun
# Required for Python multiprocessing to properly use the cores
# export MV2_ENABLE_AFFINITY=0

module load gcc/7.1.0-4bgguyp 
module load mvapich2/2.3a-avvw4kp
# module unload intel-mkl/2018.1.163-4okndez
# . /lcrc/project/EMEWS/bebop/repos/spack/share/spack/setup-env.sh
# spack load intel-mkl@2020.1.217

export R_LIBS=/lcrc/project/EMEWS/bebop/repos/spack/opt/spack/linux-centos7-broadwell/gcc-7.1.0/r-4.0.0-plchfp7jukuhu5oity7ofscseg73tofx/rlib/R/library/
export PATH=/lcrc/project/EMEWS/bebop/sfw/swift-t-9ad37bb/stc/bin:$PATH
export PYTHONHOME=/lcrc/project/EMEWS/bebop/sfw/anaconda3/2020.11

EQ_SQL=$( readlink --canonicalize $EMEWS_PROJECT_ROOT/../../EQ-SQL )
PYTHON_LIB=/lcrc/project/EMEWS/bebop/sfw/anaconda3/2020.11/lib/python3.8
export PYTHONPATH=$PYTHON_LIB:$PYTHON_LIB/site-packages:$HOME/.local/lib/python3.8/site-packages:$EQ_SQL/db:$EQ_SQL/python:$EMEWS_PROJECT_ROOT/python:$EMEWS_PROJECT_ROOT/swift/ext
# export PYTHONPATH=$EQ_SQL/db:$EQ_SQL/python:$EMEWS_PROJECT_ROOT/python:$EMEWS_PROJECT_ROOT/swift/ext
echo "PYTHONPATH: $PYTHONPATH"

MKL=/lcrc/project/EMEWS/bebop/repos/spack/opt/spack/linux-centos7-broadwell/gcc-7.1.0/intel-mkl-2020.1.217-dqzfemzfucvgn2wdx7efg4swwp6zs7ww
MKL_LIB=$MKL/mkl/lib/intel64
MKL_OMP_LIB=$MKL/lib/intel64
LDP=$MKL_LIB/libmkl_def.so:$MKL_LIB/libmkl_avx2.so:$MKL_LIB/libmkl_core.so:$MKL_LIB/libmkl_intel_lp64.so:$MKL_LIB/libmkl_intel_thread.so:$MKL_OMP_LIB/libiomp5.so
PSQL_LIB+=/lcrc/project/EMEWS/bebop/sfw/gcc-7.1.0/postgres-14.2/lib

swift-t -n $PROCS $MACHINE -p \
    -I $EMEWS_EXT \
    -e EMEWS_PROJECT_ROOT \
    -e TURBINE_OUTPUT \
    -e TURBINE_LOG \
    -e TURBINE_DEBUG \
    -e ADLB_DEBUG \
    -e DB_HOST \
    -e DB_USER \
    -e DB_PORT \
    -e PYTHONPATH \
    -e LD_LIBRARY_PATH=$PSQL_LIB:$MKL_LIB:$LD_LIBRARY_PATH \
    -e LD_PRELOAD=$LDP \
    $EMEWS_PROJECT_ROOT/swift/worker_pool_batch.swift $CMD_LINE_ARGS
