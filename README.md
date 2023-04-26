# Companion Repository for the 2023 OSPREY ParSocial Paper

This repository serves as a companion to the Collier et al. 2023 {CITATION} paper presented at the 2023 ParSocial IPDPS workshop. 

Informed by our team's work in supporting public health decision makers during the COVID-19 pandemic and by the identified capability gaps in applying high-performance computing (HPC) to computational epidemiology, the paper  presents the goals, requirements, and initial implementation of OSPREY, an open science platform for robust epidemic analysis. The prototype implementation demonstrates an integrated, algorithm-driven HPC workflow architecture, coordinating tasks across distributed HPC resources, with robust, secure and automated access to each of the resources. The paper demonstrates scalable and fault-tolerant task execution, an asynchronous API to support fast time-to-solution algorithms, an inclusive, multi-language approach, and efficient wide-area data management. This repository provides the example OSPREY code described in the paper.  

## Overview

The documentation is structured as follows. We start with a discussion of our workflow toolkit,
Extreme-scale Model Exploration with Swift (EMEWS), followed by a description of the prototype 
workflow used in the paper. We then describe the files in this repository, and then provide instructions
for how workflow can be configured and run.

## EMEWS

The Extreme-scale Model Exploration with Swift ([EMEWS](https://ieeexplore.ieee.org/document/7822090)) framework enables the direct integration of multi-language model exploration (ME) algorithms while scaling dynamic computational experiments to very large numbers (millions) of models on all major HPC platforms. EMEWS has been designed for any "black box" application code, such as agent-based and microsimulation models or training of machine learning models, that require multiple runs as part of heuristic model explorations. One of the main goals of EMEWS is to democratize the use of large-scale computing resources by making them accessible to more researchers in many more science domains. EMEWS is built on the Swift/T parallel scripting language.


EMEWS provides a high-level queue-like interface with several implementations, most notably: EQ/Py and EQ/R (EMEWS Queues for Python and R). These allow an ME (e.g., a model calibration algorithm) to push tasks (e.g., candidate model parameter inputs) to a Swift script which can then return the result of thoe tasks (e.g., model outputs) to the ME. This back-and-forth typicall continues over multiple iterations until the ME reaches some stopping condition. The tasks produced by the ME can be distributed by the Swift/T runtime over very large computer system, but smaller systems that run one model at a time are also supported. The tasks themselves can be implemented as external applications called through the shell, or in-memory libraries accessed directly by Swift (for faster invocation).

Our current queue implementation, EMEWS Queues in SQL ([EQSQL](https://github.com/emews/EQ-SQL)) builds-on
our previous work and forms the foundation of the OSPREY prototype workflow. In EQSQL, a SQL database acts as mediator between an ME that submits task to be performed and worker pool(s) that executes them. 
Submitted tasks are pushed to a database output queue table, and completed tasks to a database input
queue table. When a task has completed, it can be retrieved by the ME. The ME can also query for that status of a task, asynchronously wait for it's completion, cancel tasks, and so forth. The EQSQL API and its Python implementation is explained in greater detail in OSPREY paper[Collier et al. 2023](LINK). The EMEWS service, as described in the paper, provides remote access to the EQSQL database, and uses the EQSQL API for submitting tasks, retrieving results, and updating task priorities. 

## OSPREY Prototype Workflow

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
an initial worker pool and an EMEWS DB. When these are located on a remote resource, a funcX
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

The repository consists of the following files:

In the `python` directory:

* `ackley.py` - Python implementation of the Ackley function
* `lifecycle.py` - Python code for managing worker pools and databases, including a ssh tunnel implementation
* `emews_service.py` - EMEWS remote RESTful service for working with tasks (submitting, querying, and updating task
priorities)
* `task_queues.py` - Task API for working with tasks locally, via the EMEWS RESTful service, and using FuncX.
* `run.py` - Python workflow implementation.

In the `swift` directory:

* `worker_pool_batch.swift` - Swift/T worker pool implementation, pops tasks from the database queue, executes them, and returns the result to the database.
* `local_worker_pool.sh` - bash script for starting a local worker pool
* `bebop_worker_pool.sh` - bash script for starting a remote worker pool (e.g., on Argonne's LCRC 
Bebop cluster)

In the `swift/ext`: directory

* `emews.swift` - Utility functions used by the swift worker pools
* `eq_swift.py` - Python code used by the swift worker pools to interact with the database queues
* `EQ.swift` - Swift/T funtions for working with task queues using the code in `eq_swift.py`


In the `cfgs` directory:

* `local.cfg` - configuration file for running the local worker pool
* `test_cfg.yaml` - workflow configuration file, defines the pools, task queues, database properties, etc.

The `db` and `eqsql` directories are part of the EQSQL code and copied here for convience. The `db`
directory contains various scripts for creating and working with an EQSQL database. The `eqsql` directory
is a copy of the `esql` python package used by the workflow Python code (task queues, EMEWS service, etc.).

## Running the Workflow

Our example workflow was executed locally on an M1 MacBook Pro in conjunction with the 
University of Chicago's Midway2 HPC cluster, the Laboratory Computing
Resource Center's Bebop HPC cluster at Argonne National Laboratory, and the Argonne Leadership 
Computing Facility (ALCF) Theta supercomputer. The EMEWS DB components and worker pools were run
on Bebop, and the GPR training was done on Midway2 or Theta depending on the run configuration.

The workflow has the following external requirements:

1. Swift-t - see the installation section of the Swift-t [User Guide](http://swift-lang.github.io/swift-t/guide.html#_installation) for installation instructions. 

2. A postgresql DB - the `db` directory contains scripts for creating the database schema.

The database code expects the following environment varibles to be set.

* DB_HOST: The database host (e.g., localhost or beboplogin3)
* DB_PORT: The database port
* DB_DATA: The database "cluster" directory
* DB_NAME: The database name (e.g., EQ_SQL)
* DB_USER: The database user name

The database installation can be of two types: running on an HPC
login node where it must be manually started, or running locally as system service. For the former,
if your HPC administrators do not provide a database (e.g., via a `module load`), it can be installed from source. See
the postgresql [documentation](https://www.postgresql.org/docs/) for more info.

### Login Node Database

Assuming a working postgresql install, the database can be initialized and created using the following
steps:

Create an environment file (e.g, `my-env.sh`) specifying the DB_HOST, etc. variables for your machine. You can use the nv-example.sh as a template.

```bash
$ cd db
$ source my-env.sh
# Initialize the database "cluster" in DB_DATA
$ ./db-init.sh
$ ./db-start.sh
# Create the EQSQL tables in the databases
$ ./db-create.sh
$ ./db-stop.sh
```

Once database has been created, it can be stopped and started by sourcing
your environment file as necessary and then `db-stop.sh` and `db-start.sh`. 
`db-start.sh` will display the the current DB_PORT etc. values. These
values are required when configuring the workflow, and the current values
are written out to a `db-env-vars-TS.txt` file (where TS is a timestamp) each
time the database is started.

You can print the current contents of the database with `db-print.sh`.`db-reset.sh`
will delete the contents of the tables.

### Local Database

postgres can be installed in a local Linux distribution
using the distribution's package manager. Assuming postgres is installed
and running as a system service, the following describes how to
create an EQSQL DB.

#### Create the database cluster

```bash
# replace "12" with your postgres major version
sudo -u postgres pg_createcluster -p 5433 12 eqsql -- --auth-local trust
sudo systemctl daemon-reload
```

To start:

```bash
# replace "12" with your postgres major verion
sudo systemctl start postgresql@12-eqsql
```

#### Create db user and eqsql_db

```bash
sudo -u postgres createuser -p 5433 eqsql_user
sudo -u postgres createdb -p 5433 eqsql_db
sudo -u postgres psql
psql (12.11 (Ubuntu 12.11-0ubuntu0.20.04.1))
Type "help" for help.
postgres=# grant all privileges on database eqsql_db to eqsql_user;
GRANT
```

#### Create the EQ/SQL tables

```bash
psql --port=5433 eqsql_db eqsql_user --file db/workflow.sql
```

You can clear the tables with:

```bash
psql --port=5433 eqsql_db eqsql_user --file db/erase_tables.sql
```

If installed using the above instructions, the database will use the following environment variables:

DB_HOST=localhost
DB_USER=eqsql_user
DB_NAME=eqsql_db
DB_PORT=5433

The local database doesn't need to be stopped or started, and assuming the DB_HOST etc.,
variables have been set, You can print the current contents of the database with `db-print.sh` and `db-reset.sh` will delete the contents of the tables.

### Workflow Configuration

The workflow is configured using a yaml format configuration script. `cfgs/test_cfg.yaml` was used
to execute the workflow as described in {CITATION}. The configuration file contains both the definitions for worker pools, databases, FuncX endpoints, and proxystore, and task and task queue definitions that use them. See the OSPREY [paper](link) for the details on how these components fit together. 

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

### Running the Workflow Locally 

To run the workflow locally with a local database, and without FuncX, globus, or the emews service:

1. Setup up and start the local database as described above
2. Configure the workflow yaml with a local worker pool using `swift/local_worker_pool.sh` as the `start_pool_script`.
3. Configure the workflow yaml with a local database
4. Configure the workflow yaml with a local task_queue that uses the local database
5. Configure the workflow yaml with a task type 0 that uses the local_pool and local_db
6. Run with: `python3 python/run.py <exp_id> <path_to_config_file>` where exp_id is some unique experiment id (e.g., `test_1`)

Note `cfgs/local_cfg.yaml` is an example yaml workflow configuration for 
running locally.

