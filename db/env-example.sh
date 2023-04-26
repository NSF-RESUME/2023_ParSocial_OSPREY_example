export DB_HOST=${DB_HOST:-$(hostname --long)} # thetalogin4
# Use python to find a free port
PORT=$(python -c 'import socket; s=socket.socket(); s.bind(("", 0)); print(s.getsockname()[1]); s.close()')
export DB_PORT=${DB_PORT:-$PORT}
# The user name to use for the DB
export DB_USER=${DB_USER:-}
export DB_DATA=${DB_DATA:-<path to db data directory>}
export DB_MODE=ON

# export path to postgres sql binary
export PATH=/lcrc/project/EMEWS/bebop/sfw/gcc-7.1.0/postgres-14.2/bin:$PATH
# add postgresql lib to LD_LIBRARY_PATH - may not be necessary in some cases
export LD_LIBRARY_PATH=/lcrc/project/EMEWS/bebop/sfw/gcc-7.1.0/postgres-14.2/lib:$LD_LIBRARY_PATH
