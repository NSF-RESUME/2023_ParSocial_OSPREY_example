from random import random
import traceback
import logging
import time
import json
from datetime import datetime, timezone
from enum import IntEnum
from typing import Callable, Iterable, Tuple, Dict, List, Generator, Union

from . import db_tools
from .db_tools import WorkflowSQL


class ResultStatus(IntEnum):
    """Enum defining the status (success / failure) of an eq database
    operation.
    """
    SUCCESS = 0
    FAILURE = 1


EQ_ABORT = 'EQ_ABORT'
EQ_TIMEOUT = 'EQ_TIMEOUT'
EQ_STOP = 'EQ_STOP'


class TaskStatus(IntEnum):
    """Enum defining the status of a task: queued, etc. A database row is updated
    with these values as appropriate."""
    QUEUED = 0
    RUNNING = 1
    COMPLETE = 2
    CANCELED = 3


ABORT_JSON_MSG = json.dumps({'type': 'status', 'payload': EQ_ABORT})


class Future:

    def __init__(self, eq_sql, eq_task_id: int, tag: str = None):
        """Represents the eventual result of an EQ/SQL task. Future
        instances are created by eq.EQSQL.submit_task."""
        self.eq_task_id = eq_task_id
        self.tag = tag
        self.eq_sql = eq_sql
        self._result = None
        self._status = None
        self._pool = None

    def result(self, delay: float = 0.5, timeout: float = 2.0) -> Tuple[ResultStatus, str]:
        """Gets the result of this future task.

        This repeatedly pools the DB for the task result. The polling interval is specified by
        the delay such that the first interval is defined by the initial delay value
        which is increased after the first poll. The polling will
        timeout after the amount of time specified by the timout value is has elapsed.

    Args:
        delay: the initial polling delay value
        timeout: the duration after which the query will timeout.
            If timeout is None, there is no limit to the wait time.

    Returns:
        A tuple whose first element indicates the status of the query:
        ResultStatus.SUCCESS or ResultStatus.FAILURE, and whose second element
        is either the result of the task, or in the case of failure the reason
        for the failure (EQ_TIMEOUT, or EQ_ABORT)
        """
        # retry after an abort
        if self._result is None or self._result[1] == EQ_ABORT:
            status_result = self.eq_sql.query_result(self.eq_task_id, delay, timeout=timeout)
            if status_result[0] == ResultStatus.SUCCESS or status_result[1] == EQ_ABORT:
                self._result = status_result

            return status_result

        return self._result

    @property
    def status(self) -> TaskStatus:
        """Gets the current status of this Future, one of: eq.TaskStatus.QUEUED,
        eq.TaskStatus.RUNNING, eq.TaskStatus.COMPLETE, or eq.TaskStatus.CANCELED.

        Returns:
            One of eq.TaskStatus.QUEUED, eq.TaskStatus.RUNNING, eq.TaskStatus.COMPLETE,
            eq.TaskStatus.CANCELED or None if the status query fails.
        """
        if self._status is not None:
            return self._status

        result = self.eq_sql.query_status([self.eq_task_id])
        if result is None:
            return result
        else:
            ts = result[0][1]
            if ts == TaskStatus.COMPLETE or ts == TaskStatus.CANCELED:
                self._status = ts
            return ts

    @property
    def worker_pool(self) -> Union[str, None]:
        """Gets the id of the worker pool, if any, that this future task is
        running on.

        Returns:
            The id of the worker pool that this Future task is
            running on, or None if the task hasn't been selected
            by a worker pool yet.
        """
        if self._pool is None:
            _, pool = self.eq_sql.query_worker_pool([self.eq_task_id])[0]
            self._pool = pool
        return self._pool

    def cancel(self):
        """Cancels this future task by removing this Future's task id from the output queue.
        Cancelation can fail if this future task has been popped from the output queue
        before this call completes. Calling this on an already canceled task will return True.

        Returns:
            True if the task is canceled, otherwise False.
        """
        if self._status is not None and self._status == TaskStatus.CANCELED:
            return True

        status, row_count = self.eq_sql._cancel_tasks([self.eq_task_id])
        return status == ResultStatus.SUCCESS and row_count == 1

    def done(self):
        """Returns True if this Future task has been completed or canceled, otherwise
        False
        """
        status = self.status
        return status == TaskStatus.CANCELED or status == TaskStatus.COMPLETE

    @property
    def priority(self) -> int:
        """Gets the priority of this Future task.

        Returns:
            The priority of this Future task.
        """
        result = self.eq_sql._query_priority([self.eq_task_id])
        if result is None:
            return result
        else:
            return result[0][1]

    @priority.setter
    def priority(self, new_priority) -> ResultStatus:
        """Updates the priority of this Future task.

        Args:
            new_priority: the updated priority
        Returns:
            ResultStatus.SUCCESS if the priority has been successfully updated, otherwise false.
        """
        status, _ = self.eq_sql._update_priorities([self.eq_task_id], new_priority)
        return status


_log_id = 1


def init_eqsql(host: str, user: str, port: int, db_name: str, retry_threshold=0, log_level=logging.WARN):
    """Initializes and returns an EQSQL class instance with the specified parameters.

    Args:
        host: the eqsql database host
        user: the eqsql database user
        port: the eqsql database port
        db_name: the eqsql database name
        retry_threshold: if a DB connection cannot be established
            (e.g, there are currently too many connections),
            then retry "retry_threshold" many times to establish a connection. There
            will be random few second delay betwen each retry.
        log_level: the logging threshold level.
    """

    global _log_id
    log_name = f'{__name__}-{_log_id}'
    _log_id += 1
    logger = db_tools.setup_log(log_name, log_level)

    retries = 0
    while True:
        try:
            db = db_tools.WorkflowSQL(host=host, user=user, port=port, dbname=db_name, log_level=log_level, envs=False)
            db.connect()
            break
        except db_tools.ConnectionException as e:
            retries += 1
            if retries > retry_threshold:
                raise e
            time.sleep(random() * 4)

    return EQSQL(db, logger)


class EQSQL:

    def __init__(self, db: WorkflowSQL, logger: logging.Logger):
        self.db = db
        self.logger = logger

    def close(self):
        """Closes the DB connection. eq.init_sql() is required to re-create a new EQSQL
        before calling any other functions.
        """
        if self.db:
            self.db.close()
        self.db = None

    def _sql_pop_out_q(self, eq_type: int, n: int = 1) -> str:
        """
        Generates sql for a queue pop from emews_queue_out
        From:
        https://www.2ndquadrant.com/en/blog/what-is-select-skip-locked-for-in-postgresql-9-5
        Can only select 1 column from the subquery,
        but we return * from the deleted row.
        See workflow.sql for the returned queue row

        Args:
            eq_type: the work type to pop.
            n: the maximum number of eq_task_ids the sql query
                should return.
        Returns:
            The sql query to pop from the out queue.
        """
        code = f"""
        DELETE FROM emews_queue_OUT
        WHERE  eq_task_id = any( array(
        SELECT eq_task_id
        FROM emews_queue_OUT
        WHERE eq_task_type = {eq_type}
        ORDER BY eq_priority DESC, eq_task_id ASC
        FOR UPDATE SKIP LOCKED
        LIMIT {n}
        ))
        RETURNING *;
        """
        return code

    def _sql_pop_in_q(self, eq_task_id) -> str:
        """
        Format sql for a queue pop from emewws_queue_in
        From:
        https://www.2ndquadrant.com/en/blog/what-is-select-skip-locked-for-in-postgresql-9-5
        Can only select 1 column from the subquery,
        but we return * from the deleted row.
        See workflow.sql for the returned queue row

        Args:
            The eq_task_id to pop

        Returns:
            The sql query to pop from the in queue.
        """
        code = f"""
        DELETE FROM emews_queue_IN
        WHERE  eq_task_id = (
        SELECT eq_task_id
        FROM emews_queue_IN
        WHERE eq_task_id = {eq_task_id}
        ORDER BY eq_task_id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
        )
        RETURNING *;
        """
        return code

    def pop_out_queue(self, cur, eq_type: int, n: int, delay: float,
                      timeout: float) -> Tuple[ResultStatus, Union[int, str]]:
        """Pops the highest priority task of the specified work type off
        of the db out queue.

        This call repeatedly polls for a task of the specified type. The polling
        interval is specified by
        the delay such that the first interval is defined by the initial delay value
        which is increased exponentionally after the first poll. The polling will
        timeout after the amount of time specified by the timout value is has elapsed.

        Args:
            eq_type: the type of the work to pop from the queue
            n: the maximum number of tasks to pop from the queue
            delay: the initial polling delay value
            timeout: the duration after which this call will timeout
                and return. If timeout is None, there is no limit to
                the wait time.

        Returns: A two element tuple where the first elements is one of
            ResultStatus.SUCCESS or ResultStatus.FAILURE. On success the
            second element will be the popped eq task id. On failure, the second
            element will be one of EQ_ABORT or EQ_TIMEOUT depending on the
            cause of the failure.
        """
        sql_pop = self._sql_pop_out_q(eq_type, n)
        # print(sql_pop)
        res = self._queue_pop(cur, sql_pop, delay, timeout)
        self.logger.debug(f'pop_out_queue sql:\n{sql_pop}')
        self.logger.debug(f'pop_out_queue: {res}')
        return res

    def pop_in_queue(self, cur, eq_task_id: int, delay, timeout) -> Tuple[ResultStatus, Union[int, str]]:
        """Pops the specified task off of the db in queue.

        This call repeatedly polls for a task with specified id. The polling
        interval is specified by
        the delay such that the first interval is defined by the initial delay value
        which is increased exponentionally after the first poll. The polling will
        timeout after the amount of time specified by the timout value is has elapsed.

        Args:
            eq_task_id: id of the task to pop
            delay: the initial polling delay value
            timeout: the duration after which this call will timeout
                and return. If timeout is None, there is no limit to
                the wait time.
        Returns: A two element tuple where the first elements is one of
            ResultStatus.SUCCESS or ResultStatus.FAILURE. On success the
            second element will be eq task id. On failure, the second
            element will be one of EQ_ABORT or EQ_TIMEOUT depending on the
            cause of the failure.
        """
        sql_pop = self._sql_pop_in_q(eq_task_id)
        res = self._queue_pop(cur, sql_pop, delay, timeout)
        self.logger.debug(f'pop_in_queue: {res}')
        return res

    def _queue_pop(self, cur, sql_pop: str, delay: float,
                   timeout: float) -> Tuple[ResultStatus, Union[List[int], str]]:
        """Performs the actual queue pop as defined the sql string.

        This call repeatedly attempts the pop operation by executing sql until
        the operation completes or the timeout duration has passed. The polling
        interval is specified by
        the delay such that the first interval is defined by the initial delay value
        which is increased after the first poll. The polling will
        timeout after the amount of time specified by the timout value is has elapsed.

        Args:
            cur: the db cursor to execute the sql with
            sql_pop: the sql query that defines the pop operation
            delay: the initial polling delay value
            timeout: the duration after which this call will timeout
                and return. If timeout is None, there is no limit to
                the wait time.

        Returns: A two element tuple where the first elements is one of
            ResultStatus.SUCCESS or ResultStatus.FAILURE. On success the
            second element will be a list of the popped eq_task_ids.
            On failure, the second
            element will be one of EQ_ABORT or EQ_TIMEOUT depending on the
            cause of the failure.
        """
        start = time.time()
        results = []
        try:
            while True:
                cur.execute(sql_pop)
                # returns task_type, task_id, priority
                results = [res[1] for res in cur.fetchall()]
                # print(f'Results: {results}')
                if len(results) > 0:
                    break  # got good data
                if timeout is not None:
                    if time.time() - start > timeout:
                        return (ResultStatus.FAILURE, EQ_TIMEOUT)
                time.sleep(delay)
                if delay < 30:
                    delay += 0.25
        except Exception as e:
            self.logger.error(f'queue_pop error {traceback.format_exc()}')
            raise e

        return (ResultStatus.SUCCESS, results)

    def push_out_queue(self, cur, eq_task_id: int, eq_type: int, priority: int = 0):
        """Pushes the specified task onto the output queue with
        the specified priority.

        Args:
            cur: the database cursor used to execute the sql push
            eq_task_id: the id of the task
            eq_type: the type of the task
            priority: the priority of the task
        """
        try:
            # queue_push("emews_queue_OUT", eq_type, eq_task_id, priority)
            insert_cmd = db_tools.format_insert('emews_queue_out', ["eq_task_type", "eq_task_id",
                                                "eq_priority"])
            cur.execute(insert_cmd, [eq_type, eq_task_id, priority])
            update_cmd = db_tools.format_update('eq_tasks', ['eq_status'], where='eq_task_id=%s')
            cur.execute(update_cmd, [TaskStatus.QUEUED.value, eq_task_id])

        except Exception as e:
            self.logger.error(f'push_out_queue error {traceback.format_exc()}')
            raise e

    def push_in_queue(self, cur, eq_task_id: int, eq_type: int) -> ResultStatus:
        """Pushes the specified task onto the input queue.

        Args:
            cur: the database cursor used to execute the sql push
            eq_task_id: the id of the task
            eq_type: the type of the task
        Returns:
            ResultStatus.SUCCESS if the task was successfully pushed
            onto the input queue, otherwise ResultStatus.FAILURE.
        """
        try:
            cmd = db_tools.format_insert('emews_queue_IN', ["eq_task_type", "eq_task_id"])
            cur.execute(cmd, [eq_type, eq_task_id])
            return ResultStatus.SUCCESS
        except Exception:
            self.logger.error(f'push_in_queue error {traceback.format_exc()}')
            return ResultStatus.FAILURE

    def _insert_task(self, cur, exp_id: str, eq_type: int, payload: str) -> int:
        """Inserts the specified payload to the database, creating
        a task entry for it and returning its assigned task id

        Args:
            cur: the database cursor used to execute the insert
            exp_id: the id of the experiment that this task is part of
            eq_type: the work type of this task
            payload: the task payload

        Returns:
            The task id assigned to this task if the insert
            was successfull, otherwise raise an exception.
        """
        try:
            cur.execute("select nextval('emews_id_generator');")
            rs = cur.fetchone()
            eq_task_id = rs[0]
            ts = datetime.now(timezone.utc).astimezone().isoformat()
            insert_cmd = db_tools.format_insert("eq_tasks", ["eq_task_id", "eq_task_type",
                                                "json_out", "time_created"])
            cur.execute(insert_cmd, [eq_task_id, eq_type, payload, ts])
            insert_cmd = db_tools.format_insert("eq_exp_id_tasks", ["exp_id", "eq_task_id"])
            cur.execute(insert_cmd, [exp_id, eq_task_id])
        except Exception as e:
            self.logger.error(f'insert_task error {traceback.format_exc()}')
            raise e

        return eq_task_id

    def select_task_payload(self, cur, eq_task_ids: Iterable[int], worker_pool_id: str = 'default') -> List[Tuple[int, str]]:
        """Selects the 'json_out' payload associated with the specified task ids in
        the eq_tasks table, setting the start time of the tasks to
        the current time, the status of the tasks to TaskStatus.RUNNING, and the
        worker_pool to the specified worker_pool.

        Args:
            cur: the database cursor used to execute the sql select
            eq_task_ids: the ids of the tasks to get the json_out for
            worker_pool_id: the id of the worker pool asking for the payload

        Returns:
            If successful, a list of tuples containing an eq_task_id and
            the json_out payload, otherwise raise an exception.
        """

        placeholders = ', '.join(['%s'] * len(eq_task_ids))
        task_ids = list(eq_task_ids)
        try:
            query = f'select eq_task_id, json_out from eq_tasks where eq_task_id in ({placeholders}) ORDER BY eq_task_id ASC'
            cur.execute(query, task_ids)
            result = [(rs[0], rs[1]) for rs in cur.fetchall()]
            # cmd = db_tools.format_select("eq_tasks", "json_out", 'eq_task_id=%s')
            # cur.execute(cmd, (eq_task_id,))
            # rs = cur.fetchone()
            ts = datetime.now(timezone.utc).astimezone().isoformat()
            cmd = f'update eq_tasks set eq_status=%s, worker_pool=%s, time_start=%s where eq_task_id in ({placeholders})'
            cur.execute(cmd, [TaskStatus.RUNNING.value, worker_pool_id, ts] + task_ids)
            return result
        except Exception as e:
            self.logger.error(f'select_task_payload error {traceback.format_exc()}')
            raise e

    def select_task_result(self, cur, eq_task_id: int) -> str:
        """Selects the result ('json_in') payload associated with the specified task id in
        the eq_tasks table.

        Args:
            eq_task_id: the id of the task to get the json_in for

        Returns:
            The result payload for the specified task id, if successfull, otherwise
            raise an exception.
        """
        try:
            select_sql = db_tools.format_select("eq_tasks", "json_in", 'eq_task_id=%s')
            cur.execute(select_sql, (eq_task_id,))
            rs = cur.fetchone()
            result = rs[0]
        except Exception as e:
            self.logger.error(f'select_task_result error {traceback.format_exc()}')
            raise e

        return result

    def update_task(self, cur, eq_task_id: int, payload: str):
        """Updates the specified task in the eq_tasks table with the specified
        result ('json_in') payload. This also updates the 'time_stop'
        to the time when the update occurred.

        Args:
            eq_task_id: the id of the task to update
            payload: the payload to update the task with
        """
        ts = datetime.now(timezone.utc).astimezone().isoformat()
        try:
            cmd = db_tools.format_update("eq_tasks", ['json_in', 'eq_status', 'time_stop'],
                                         where='eq_task_id=%s')
            cur.execute(cmd, [payload, TaskStatus.COMPLETE.value, ts, eq_task_id])
        except Exception as e:
            self.logger.error(f'update_task error {traceback.format_exc()}')
            raise e

    def stop_worker_pool(self, eq_type: int) -> ResultStatus:
        """Stops any workers pools associated with the specified work type by
        pusing EQ_STOP into the queue.

        Args:
            eq_type: the work type for the pools to stop
        Returns:
            ResultStatus.SUCCESS if the stop message was successfully pushed, otherwise
            ResultStatus.FAILURE.
        """
        try:
            with self.db.conn:
                with self.db.conn.cursor() as cur:
                    cur.execute("select nextval('emews_id_generator');")
                    rs = cur.fetchone()
                    eq_task_id = rs[0]
                    cmd = db_tools.format_insert("eq_tasks", ["eq_task_id", "eq_task_type", "json_out"])
                    cur.execute(cmd, [eq_task_id, eq_type, EQ_STOP])
                    self.push_out_queue(cur, eq_task_id, eq_type, priority=-1)
                    return ResultStatus.SUCCESS
        except Exception:
            self.logger.error(f'stop_worker_pool error {traceback.format_exc()}')
            return ResultStatus.FAILURE

    def submit_task(self, exp_id: str, eq_type: int, payload: str, priority: int = 0, tag: str = None) -> Future:
        """Submits work of the specified type and priority with the specified
        payload, returning the status and the Future encapsulating the submission.

        Args:
            exp_id: the id of the experiment of which the work is part.
            eq_type: the type of work
            payload: the work payload
            priority: the priority of this work

        Returns:
            A tuple containing the status (ResultStatus.FAILURE or ResultStatus.SUCCESS) of the submission
            and if successful, a Future representing the submitted task otherwise None.
        """
        try:
            with self.db.conn:
                with self.db.conn.cursor() as cur:
                    eq_task_id = self._insert_task(cur, exp_id, eq_type, payload)
                    cmd = db_tools.format_insert('eq_task_tags', ['eq_task_id', 'tag'])
                    cur.execute(cmd, (eq_task_id, tag))
                    self.push_out_queue(cur, eq_task_id, eq_type, priority)
                    return (ResultStatus.SUCCESS, Future(self, eq_task_id, tag))
        except Exception:
            self.logger.error(f'submit_task error {traceback.format_exc()}')
            return (ResultStatus.FAILURE, None)

    def query_more_tasks(self, eq_type: int, eq_task_ids: Iterable[int], batch_size: int, threshold: int = 1,
                         worker_pool: str = 'default', delay: float = 0.5, timeout: float = 2.0) -> Tuple[List[int], List[Dict]]:
        """Queries for tasks of the specified type, returning up to batch_size number of tasks. The
        exact number of task to return is batch_size - X where X is the number of currently running tasks
        in the tasks in eq_task_ids. The intention here is that worker pool may have a limited amount
        of capacity and shouldn't get more work than that. eq_task_ids keeps track of the number
        tasks the worker pool is working on and batch_size is the maximum amount of work (tasks)
        the worker pool wants to execute. When the difference between batch size and the number of
        running tasks is greater than the threshold then query for tasks.

        The query repeatedly polls for tasks. The polling interval is specified by
        the delay such that the first interval is defined by the initial delay value
        which is increased exponentionally after the first poll. The polling will
        timeout after the amount of time specified by the timout value is has elapsed.

        Args:
            eq_type: the type of the work to query for.
            eq_task_ids: the possibly running task ids used to determine the number of tasks to return.
            batch_size: the maximum amount of tasks to return
            threshold: the number of free "slots" (difference between running workers and batch_size)
                that must be available before tasks are queried for. This must be less than or equal to
                batch size.
            worker_pool: the id of the worker pool query for the tasks
            delay: the initial polling delay value
            timeout: the duration after which the query will timeout. If timeout is None, there is no limit to
                the wait time.


        Returns:
            A two element Tuple where the first element is a List of the ids of the currently running tasks
            from those specified in eq_task_ids plus the ids of any new tasks, and the second element
            is a List of Dictionaries as returned by :func: `~eqsql.eq.query_task`.
        """
        if threshold < 1:
            raise ValueError(f'Invalid threshold: threshold must be greater than 0: threshold = {threshold}')

        if batch_size < 1:
            raise ValueError(f'Invalid batch_size: batch_size must be greater than 0: batch_size = {batch_size}')

        if threshold > batch_size:
            raise ValueError('Invalid threshold / batch_size: threshold of must be less than '
                             f'or equal to batch_size: threshold = {threshold}, batch_size = {batch_size}')

        running_tasks = []
        if len(eq_task_ids) > 0:
            statuses = self.query_status(eq_task_ids)
            if statuses is None:
                return ([], [{'type': 'status', 'payload': EQ_ABORT}])

            running_tasks = [eq_task_id for eq_task_id, _ in
                             filter(lambda item: item[1] == TaskStatus.RUNNING, statuses)]

        # print("batch size", batch_size, flush=True)
        n_query = batch_size - len(running_tasks)
        # print(f'n_query: {n_query}', flush=True)
        if n_query >= threshold:
            new_tasks = self.query_task(eq_type, n=n_query, worker_pool=worker_pool, delay=delay, timeout=timeout)
            if n_query == 1:
                if 'eq_task_id' in new_tasks:
                    return (running_tasks + [new_tasks['eq_task_id']], [new_tasks])
                else:
                    # stop or abort msg
                    return (running_tasks, [new_tasks])
            else:
                # can be single dictionary if abort or timeout
                if not isinstance(new_tasks, List):
                    new_tasks = [new_tasks]
                return (running_tasks + [task['eq_task_id'] for task in
                        filter(lambda task: 'eq_task_id' in task, new_tasks)],
                        new_tasks)
        else:
            return (running_tasks, [])

    def query_task(self, eq_type: int, n: int = 1, worker_pool: str = 'default', delay: float = 0.5, timeout: float = 2.0) -> Union[List[Dict], Dict]:
        """Queries for the highest priority task of the specified type.

        The query repeatedly polls for n number of tasks. The polling interval is specified by
        the delay such that the first interval is defined by the initial delay value
        which is increased exponentionally after the first poll. The polling will
        timeout after the amount of time specified by the timout value is has elapsed.

        Args:
            eq_type: the type of the task to query for
            n: the maximum number of tasks to query for
            worker_pool: the id of the worker pool query for the tasks
            delay: the initial polling delay value
            timeout: the duration after which the query will timeout. If timeout is None, there is no limit to
                the wait time.

        Returns:
            If n == 1, a single dictionary will be returned, otherwise
            a List of dictionaries will be returned. In both cases, the
            dictionary(ies) contain the following. If the query results in a
            status update, the dictionary will have the following format:
            {'type': 'status', 'payload': P} where P is one of 'EQ_STOP',
            'EQ_ABORT', or 'EQ_TIMEOUT'. If the query finds work to be done
            then the dictionary will be:  {'type': 'work', 'eq_task_id': eq_task_id,
            'payload': P} where P is the parameters for the work to be done.
        """
        try:
            with self.db.conn:
                with self.db.conn.cursor() as cur:
                    status, result = self.pop_out_queue(cur, eq_type, n, delay, timeout)
                    self.logger.info(f'MSG: {status} {result}')
                    if status == ResultStatus.SUCCESS:
                        eq_task_ids = result
                        payloads = self.select_task_payload(cur, eq_task_ids, worker_pool)
                        results = []
                        for task_id, payload in payloads:
                            if payload == EQ_STOP:
                                results.append({'type': 'status', 'payload': EQ_STOP})
                            else:
                                results.append({'type': 'work', 'eq_task_id': task_id,
                                                'payload': payload})
                        if n == 1:
                            return results[0]
                        else:
                            return results
                    else:
                        # timed out
                        return {'type': 'status', 'payload': result}
        except Exception:
            return {'type': 'status', 'payload': EQ_ABORT}

    def report_task(self, eq_task_id: int, eq_type: int, result: str) -> ResultStatus:
        """Reports the result of the specified task of the specified type

        Args:
            eq_task_id: the id of the task whose results are being reported.
            eq_type: the type of the task whose results are being reported.
            result: the result of the task.
        Returns:
            ResultStatus.SUCCESS if the task was successfully reported, otherwise
            ResultStatus.FAILURE.
        """
        # We do this is in two transactions so if push_in_queue fails, we don't
        # rollback update_task and lose a task result.
        try:
            with self.db.conn:
                with self.db.conn.cursor() as cur:
                    self.update_task(cur, eq_task_id, result)
        except Exception:
            self.logger.error(f'report_task error {traceback.format_exc()}')
            return ResultStatus.FAILURE

        try:
            with self.db.conn:
                with self.db.conn.cursor() as cur:
                    return self.push_in_queue(cur, eq_task_id, eq_type)
        except Exception:
            self.logger.error(f'report_task error {traceback.format_exc()}')
            return ResultStatus.FAILURE

    def are_queues_empty(self) -> bool:
        """Gets whether or not either of the input or output queues are empty.

        Returns:
            True if the queues are empty, otherwise False.
        """
        empty = True
        with self.db.conn:
            with self.db.conn.cursor() as cur:
                tables = ["emews_queue_in", "emews_queue_out"]
                for table in tables:
                    cur.execute(f"select count(eq_task_id) from {table};")
                    rs = cur.fetchone()
                    count = rs[0]
                    if count > 0:
                        # print(f": There are entries in table '{table}'")
                        empty = False

        return empty

    def query_status(self, eq_task_ids: Iterable[int]) -> List[Tuple[int, TaskStatus]]:
        """Queries for the status (queued, running, etc.) of the specified tasks

        Args:
            eq_task_ids: the ids of the tasks to query the status of

        Returns:
            A List of Tuples containing the status of the tasks. The first element
            of the tuple will be the task id, and the second element will be the
            status of that task as a TaskStatus object.
        """
        ids = tuple(eq_task_ids)
        placeholders = ', '.join(['%s'] * len(ids))
        try:
            with self.db.conn:
                with self.db.conn.cursor() as cur:
                    query = f'select eq_task_id, eq_status from eq_tasks where eq_task_id in ({placeholders})'
                    cur.execute(query, ids)
                    results = [(eq_task_id, TaskStatus(status)) for eq_task_id, status in cur.fetchall()]
        except Exception:
            self.logger.error(f'query_status error: {traceback.format_exc()}')
            return None

        return results

    def query_worker_pool(self, eq_task_ids: Iterable[int]) -> List[Tuple[id, Union[str, None]]]:
        """Gets the worker pools on which the specified tasks are running, if any.

        Returns:
            A list of two element tuples. The first element is the task id, and the
            second is that task's worker pool, or None, if the task hasn't been
            selected for execution yet.
        """
        ids = tuple(eq_task_ids)
        placeholders = ', '.join(['%s'] * len(ids))
        try:
            with self.db.conn:
                with self.db.conn.cursor() as cur:
                    query = f'select eq_task_id, worker_pool from eq_tasks where eq_task_id in ({placeholders})'
                    cur.execute(query, ids)
                    results = [(eq_task_id, wp) for eq_task_id, wp in cur.fetchall()]
        except Exception:
            self.logger.error(f'query_worker_pool error: {traceback.format_exc()}')
            return None

        return results

    def _cancel_tasks(self, eq_task_ids: Iterable[int]) -> Tuple[ResultStatus, int]:
        """Cancels the specified tasks by removing them from the output queue and
        marking their status as canceled.

        Args:
            eq_task_ids: the ids of the tasks to cancel.

        Returns:
            If the cancel is successful, the Tuple will contain ResultStatus.SUCCESS and
            the number of tasks that were canceled, otherwise (ResultStatus.FAILURE, -1).
        """
        ids = tuple(eq_task_ids)
        placeholders = ', '.join(['%s'] * len(ids))
        # delete should lock all the rows, so they can't be selected
        update_query = f'update eq_tasks set eq_status = {TaskStatus.CANCELED.value} where '\
            f'eq_task_id in  ({placeholders});'
        delete_query = f'delete from emews_queue_out where eq_task_id in ({placeholders});'
        try:
            with self.db.conn:
                with self.db.conn.cursor() as cur:
                    cur.execute(delete_query, ids)
                    deleted_rows = cur.rowcount
                    cur.execute(update_query, ids)

        except Exception:
            self.logger.error(f'cancel task error: {traceback.format_exc()}')
            return (ResultStatus.FAILURE, -1)

        return (ResultStatus.SUCCESS, deleted_rows)

    def _update_priorities(self, eq_task_ids: Iterable[int], new_priority: Union[int, List[int]]) -> Tuple[ResultStatus, int]:
        """Updates the priorities of the specified tasks.

        Args:
            eq_task_ids: the ids of the tasks whose priorities should be updated.
            new_priority: the new priority for the specified tasks. If this is a single integer then
            all the specified tasks are updated with that priority. If this is a
            List of ints then each task is updated with the corresponding priority, i.e.,
            the first task in the eq_task_ids is updated with the first priority in the new_priority
            List.

        Returns:
            If the update is successful, the Tuple will contain ResultStatus.SUCCESS and
            the number of tasks whose priorities were successfully updated,
            otherwise (ResultStatus.FAILURE, -1).
        """

        # update emews_queue_out as u set
        # eq_priority = u2.priority
        # from (values
        # (1, 3),
        # (2, 5),
        # (3, 2)) as u2(id, priority) where u2.id = eq_task_id;
        ids = tuple(eq_task_ids)
        try:
            with self.db.conn:
                with self.db.conn.cursor() as cur:
                    if isinstance(new_priority, int):
                        placeholders = ', '.join(['%s'] * len(ids))
                        query = f'update emews_queue_out set eq_priority = %s where eq_task_id in ({placeholders})'
                        cur.execute(query, (new_priority,) + ids)
                        updated_rows = cur.rowcount
                    else:
                        if len(ids) != len(new_priority):
                            raise ValueError("Number of task ids and updated priorities must be equal")
                        placeholders = ', '.join(['(%s, %s)'] * len(ids))
                        query = f"""update emews_queue_out as u set
                                eq_priority = u2.priority
                                from (values
                                {placeholders}
                                ) as u2(id, priority) where u2.id = eq_task_id;
                                """
                        cur.execute(query, [y for x in zip(ids, new_priority) for y in x])
                        updated_rows = cur.rowcount
        except Exception:
            self.logger.error(f'update_priority error: {traceback.format_exc()}')
            return (ResultStatus.FAILURE, -1)

        return (ResultStatus.SUCCESS, updated_rows)

    def _query_priority(self, eq_task_ids: Iterable[int]) -> List[Tuple[int, int]]:
        """Gets the priorities of the specified tasks.

        Args:
            eq_task_ids: the ids of the tasks whose priorities are returned.

        Returns:
            A List of tuples containing the task_id and priorty for each task, or None if the
            query has failed.
        """
        ids = tuple(eq_task_ids)
        placeholders = ', '.join(['%s'] * len(ids))
        results = []
        try:
            with self.db.conn:
                with self.db.conn.cursor() as cur:
                    query = f'select eq_task_id, eq_priority from emews_queue_out where eq_task_id in ({placeholders})'
                    cur.execute(query, ids)
                    for eq_task_id, priority in cur.fetchall():
                        results.append((eq_task_id, priority))
        except Exception:
            self.logger.error(f'query_priority error: {traceback.format_exc()}')
            return None

        return results

    def query_result(self, eq_task_id: int, delay: float = 0.5, timeout: float = 2.0) -> Tuple[ResultStatus, str]:
        """Queries for the result of the specified task.

        The query repeatedly polls for a result. The polling interval is specified by
        the delay such that the first interval is defined by the initial delay value
        which is increased after the first poll. The polling will
        timeout after the amount of time specified by the timout value is has elapsed.

        Args:
            eq_task_id: the id of the task to query
            delay: the initial polling delay value
            timeout: the duration after which the query will timeout. If timeout is None, there is no limit to
                the wait time.

        Returns:
            A tuple whose first element indicates the status of the query:
            ResultStatus.SUCCESS or ResultStatus.FAILURE, and whose second element
            is either the result of the task, or in the case of failure the reason
            for the failure (EQ_TIMEOUT, or EQ_ABORT)
        """
        try:
            with self.db.conn:
                with self.db.conn.cursor() as cur:
                    msg = self.pop_in_queue(cur, eq_task_id, delay, timeout)
                    if msg[0] != ResultStatus.SUCCESS:
                        return msg

                    return (ResultStatus.SUCCESS, self.select_task_result(cur, eq_task_id))
        except Exception:
            self.logger.error(f'query_result error {traceback.format_exc()}')
            return (ResultStatus.FAILURE, EQ_ABORT)


def as_completed(futures: List[Future], pop: bool = False, timeout: float = None, n: int = None,
                 stop_condition: Callable = None, sleep: float = 0) -> Generator[Future, None, None]:
    """Returns a generator over the futures given by the futures argument that yields
    futures as they complete. The futures are checked for completion by iterating over all of the
    ones that have not yet completed and checking for a result. At the end of each iteration, the
    stop_condition and timeout are checked. Note that adding or removing Futures to or from the futures
    argument List will have NO effect on this call as it operates on a copy of the futures List.
    A TimeoutError will be raised if the futures do not complete within the specified timeout duration.
    If the stop_condition is not None, it will be called after every iteration through the futures.
    If it returns True, then a StopConditionException will be raised.

    Args:
        futures: the List of Futures to iterate over and return as they complete.
        pop: if true, completed futures will be popped off of the futures argument List.
        timeout: if the time taken for futures to completed is greater than this value, then
            raise TimeoutError.
        n: yield up to this many completed Futures and then stop iteration.
        stop_condition: this Callable will be called after each check of all the futures and a
            StopConditionException will be raised if the return value is True.
        sleep: the time, in seconds, to sleep between each iteration over all the Futures.

    """
    start_time = time.time()
    completed_tasks = set()
    wk_futures = [f for f in futures]
    n_futures = len(wk_futures)
    while True:
        for f in wk_futures:
            if f.eq_task_id not in completed_tasks:
                status, result_str = f.result(timeout=0.0)
                if status == ResultStatus.SUCCESS or result_str == EQ_ABORT:
                    completed_tasks.add(f.eq_task_id)
                    if pop:
                        futures.remove(f)
                    yield f
                    n_completed = len(completed_tasks)
                    if n_completed == n_futures or n_completed == n:
                        # Python docs: return rather than raise StopIteration
                        return

            if timeout is not None and time.time() - start_time > timeout:
                raise TimeoutError(f'as_completed timed out after {timeout} seconds')

        if stop_condition is not None and stop_condition():
            raise StopConditionException('as_completed stopped due stop condition')
        if sleep > 0:
            time.sleep(sleep)


def pop_completed(futures: List[Future], timeout=None, sleep: float = 0):
    """Pops and returns the first completed future from the specified List
    of Futures.

    Args:
        futures: the List of Futures to check for a completed one. The completed
            future will be popped from this list.
        timeout: a TimeoutError will be raised if a completed Future cannot be returned
            by after this amount time.
        sleep: the time, in seconds, to sleep between each iteration over all the Futures,
            when looking for a completed one.
    """
    f = next(as_completed(futures, pop=True, timeout=timeout, n=1, sleep=sleep))
    return f


def cancel(futures: List[Future]) -> int:
    """Cancels the specified Futures.

    Args:
        futures: the Futures to cancel.

    Returns:
        The ResultStatus and number tasks successfully canceled.
    """
    if len(futures) > 0:
        return futures[0].eq_sql._cancel_tasks((f.eq_task_id for f in futures))

    return (ResultStatus.SUCCESS, 0)


def update_priority(futures: List[Future], new_priority: Union[int, List[int]]) -> int:
    """Updates the priority of the specified Futures to the new_priority.

    Args:
        futures: the Futures to update.
        new_priority: the priority to update to. If this is a single integer then
            all the specified tasks are updated with that priority. If this is a
            List of ints then each task is updated with the corresponding priority, i.e.,
            the first task in the eq_task_ids is updated with the first priority in the new_priority
            List.

    Returns:
        The ResultStatus and number tasks successfully whose priority was
            successfully updated.
    """
    if len(futures) > 0:
        return futures[0].eq_sql._update_priorities((f.eq_task_id for f in futures), new_priority)

    return (ResultStatus.SUCCESS, 0)


def query_worker_pool(futures: List[Future]) -> List[Tuple[Future, Union[str, None]]]:
    """Gets the worker pools on which the specified list of Futures are running, if any.
    All the specified Futures much have been submitted by the same task queue (i.e., they
    are all in the same eqsql database).

    Returns:
        A list of two element tuples. The first element is a Future, and the
        second is that Future's worker pool, or None, if the Future hasn't been
        selected for execution yet.
    """
    ids = {ft.eq_task_id: ft for ft in futures}
    results = futures[0].eq_sql.query_worker_pool(ids.keys())
    return [(ids[task_id], pool) for task_id, pool in results]


class StopConditionException(Exception):
    def __init__(self, msg='StopIterationException', *args, **kwargs):
        super().__init__(msg, *args, **kwargs)
        pass


class TimeoutError(Exception):
    def __init__(self, msg='TimeoutError', *args, **kwargs):
        super().__init__(msg, *args, **kwargs)
        pass
