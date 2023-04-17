# Prototype Workflow for the 2023 ParSocial Paper

Our prototype workflow implements an example optimization workflow
that attempts to find the minimum of the Ackley function using a 
Gaussian process regression model (GPR). Our implementation,
is based on a similar example problem provided as part of the Colmena [documentation](https://github.com/exalearn/colmena/blob/bd334e0a582fb79d97652d67d05666f13d178f83/demo_apps/optimizer-examples/streaming.py#L1).
We begin with a sample set containing a number of randomly generated n-dimensional points. 
Each of these points is submitted as a task to the Ackley function for evaluation. When
a specified number of tasks have completed (i.e., that number of Ackley function evaluation results
are available), we train a GPR using the results, and 
reorder the evaluation of the remaining tasks, increasing the priority of those more
likely to find an optimal result according to the GPR. This repeats until all the evaluations complete.


The model exploration (ME) algorithm is a Python script (`python/run.py`) that begins by initializing
an initial worker pool and an EMEWS DB. When these are located on a remote resource, a funcx
client, and the EMEWS service, which mediates between the remote DB and the local model exploration
algorithm, and an SSH tunnel through which we communicate
with the EMEWS service are started. After initializing, we create an initial sample set of 750 4-dimensional points, which
are submitted as tasks. The worker pool
pops these tasks off the database output queue, and executes the Ackley function (`python/ackley.py`) using the point
data in the tasks' payload. (We have added a lognormally distributed sleep delay to the Ackley function implementation to increase the otherwise millisecond runtime and to add task runtime heterogeneity
for demonstration purposes.) On completion, the task results are pushed onto the database's 
input queue. While the worker pool is executing the tasks, the local Python script (`python/run.py`),
waits for the next 50 tasks to complete at which time we perform the reprioritization. The completed 
tasks are 
popped off the list of futures returned by the submission using the `as_completed` API function (`python/task_queues.py`).

The reprioritization consists of retraining the GPR with the completed results and then updating the
evaluation priorities of the uncompleted tasks using the GPR predictions. The retraining of the GPR
can be performed remotely using funcX or locally as part of the ME script and returns the updated evaluation order. When run remotely, the GPR itself was passed as a ProxyStore proxy
object, using ProxyStore's Globus functionality, to the reprioritization function and resolved into the actual GPR during remote function evaluation.
Using the updated order returned from the function, the uncompleted tasks are reprioritized using the
`update_priorities` API function (`python/task_queues.py`). The reprioritization repeats for every new 50 completed tasks, and start an additional worker pool after the 2nd and 4th
reprioritizations, for a final total of 3 worker pools. Connecting to the same database as the initial worker pool, these worker pools perform
the same type of work, popping
tasks off the same output queue, and executing the Ackley function (`python/ackley.py`) using those tasks' payload.
When there are no more tasks left to complete, the workflow terminates, stopping the database, and shutting
down any funcX executors.

## Files

`python/`:

* `ackley.py` - Python implementation of the Ackley function
* `lifecycle.py` - Python code for managing worker pools and databases, including a ssh tunnel implementation
* `emews_service.py` - EMEWS remote RESTful service for working with tasks (submitting, querying, and updating task
priorities)
* `task_queues.py` - Task API for working with tasks locally, via the EMEWS RESTful service, and using FuncX.
* `run.py` - Python workflow implementation.

`swift/`:

* `worker_pool_batch.swift` - Swift/T worker pool implementation, pops tasks from the database queue, executes them, and returns the result to the database.
* `local_worker_pool.sh` - bash script for starting a local worker pool
* `bebop_worker_pool.sh` - bash script for starting a remote worker pool (e.g., on Argonne's LCRC 
Bebop cluster)

`swift/ext/`:

* `emews.swift` - Utility functions used by the swift worker pools
* `eq_swift.py` - Python code used by the swift worker pools to interact with the database queues
* `EQ.swift` - Swift/T funtions for working with task queues using the code in `eq_swift.py`


`cfgs/`:
* `local.cfg` - configuration file for running the local worker pool
* `test_cfg.yaml` - workflow configuration file, defines the pools, task queues, database properties, etc.


## Running the Workflow

Our example workflow was executed locally on an M1 MacBook Pro in conjunction with the 
University of Chicago's Midway2 HPC cluster, the Laboratory Computing
Resource Center's Bebop HPC cluster at Argonne National Laboratory, and the Argonne Leadership 
Computing Facility (ALCF) Theta supercomputer. The EMEWS DB components and worker pools were run
on Bebop, and the GPR training was done on Midway2 or Theta depending on the run configuration.

The workflow is configured using a yaml format configuration script. `cfgs/test_cfg.yaml` was used
to execute the workflow as described in {CITATION}. The configuration file contains both the definitions for worker pools, databases, FuncX endpoints, and proxystore, and task and task queue definitions that use them.

### Worker Pools Configuration

Worker pools are defined in the `pools` entry of the configuration file, and have the following format:

```yaml
pools:
    <name>:
        type: local | funcx
        fx_endpoint: <funcx endpoint name, when type is funcx>
        start_pool_script: <script to start the worker pool>
        CFG_NODES: <number of nodes to run the pool on>
        CFG_PPN: <number of procs per node>
        CFG_PROCS: $(( CFG_NODES * CFG_PPN ))
        CFG_BATCH_SIZE: <number of tasks to own at a time>
        CFG_BATCH_THRESHOLD: <threshold deficit between owned and requested tasks>
        # Required when workerpool is started on remote scheduled system via funcx:
        CFG_WALLTIME: <scheduler walltime>
        CFG_QUEUE: <queue to run the p0ol on>
        CFG_PROJECT: <the project under which the pool will run>
    <name>:
        type: local | funcx
        ...
```

### EQSQL Database Configuration

The EQSQL databases are defined in the `dbs` entry.

```yaml
dbs:
    <name>:
        type: local | funcx
        fx_endpoint: <funcx endpoint name>
        db_user: <database user>
        db_port: <database port>
        db_name: <datbase name>
        db_data: <database data directory>
        # Required when database is remote and started via funcx:
        env:
            <environment var name>: <environment var value>
            ...
        start_cmd: <the command used to start the database>
        stop_cmd: <the command used to stop the database>
```

Note that the `start_cmd` and `stop_cmd` can contain the db_* yaml configuration values,
using a `${x}` syntax. For example, `${db_data}` in a `start_cmd` will be replaced with 
the `db_data` value.

### FuncX Endpoint Configuration

FuncX endpoints are defined in the `fx_endpoints` entry.

```yaml
fx_endpoints:
    <name>: <funcx endpoint id>
    ...
```

The `fx_endpoint` entries in the worker pools and database configuration sections are expected to
refer to the named endpoints defined here.

### ProxyStore Configuration

Proxystore endpoints are defined in the `proxystore` entry. 

```yaml
proxystore:
    globus_config:
        <globus endpoint dictionary>
```

The `globus endpoint dictionary` format is defined in the proxy store [documentation](https://proxystore.readthedocs.io/en/latest/api/proxystore.store.globus.html#module-proxystore.store.globus).


### Tasks Configuration

The `tasks` entry defines a task type, which pool(s) will consume tasks of that type and what database will
be used by those pool(s). The workflow code (i.e., `run.py`) will start the pools and databases named
in the `tasks` entry, using the defintions provided in the `pools` and `dbs` sections. 

```yaml
tasks:
    - type: <integer type id>
        pools:
            - <pool name 1>
            - <pool name 2>
            - ...
        db: <db name>
```

### Task Queues Configuration

The `tasks_queues` entry define named queues for submitting work for execution. The task queue implementations are defined in `python/task_queues.py`, and depending on the type specified in the
yaml configuration a `LocalTaskQueue`, a `RESTfulTaskQueue`, or a `FXTaskQueue` will be
instantiated. The `LocalTaskQueue` communicates directly with a local database using the eqsql API.
The `RESTfulTaskQueue` communicates with the EMEWS service through a SSH tunnel. The `FXTaskQueue`
calls the EMEWS service task submission etc. functions directly using FuncX rather through the EMEWS
service REST API.

```yaml
task_queues:
    <name>:
        db: <db name>
        type: local | funcx | emews_service
        # Required for funcx and emews_service types
        fx_endpoint: <funcx endpoint name>
        # Required for emews_service type
        host: <emews service remote host ip address>
        port: <emews service remote port>
        tunnel:
            config_key: <ssh host key value>
            ssh_config: <ssh config file name>
            port: <ssh tunnel local port>
```

Note: the `emews_service` type assumes that the emews service python code is importable by
the FuncX endpoint specified in the `fx_endpoint` entry.

### Remote Retraining Configuration

As mentioned above, the training portion of task reprioritization can be performed
remotely. The `reprioritization_endpoint` entry specifies a FuncX endpoint to perform
the retraining. 

```yaml
reprioritize_endpoint: <fx endpoint name>
```

### Model Exploration Parameters

The yaml configuration file also contains input parameters for the workflow model exploration algorithm
itself. 

```yaml
search_space_size: <number of points in the starting sample set>
sample_dimensions: <number of dimensions of each point>
runtime: <mean runtime sleep delay>
runtime_var: <runtime sleep delay standard deviation>
num_guesses: <total number of tasks to complete>
retrain_after: <retrain and reprioritize each time this number of tasks complete>
```

## Running the Workflow Locally 

TODO: a bit more info here

1. Pull EQSQL repo
2. Setup DB
3. Create yaml configuration
4. python3 run.py <experiment_id> <config.yaml>
