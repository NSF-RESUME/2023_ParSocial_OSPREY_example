import argparse
import yaml
from typing import Dict, List
import numpy as np
import json

from sklearn.gaussian_process import GaussianProcessRegressor, kernels
from sklearn.preprocessing import MinMaxScaler
from sklearn.pipeline import Pipeline

from proxystore.store import get_store

import lifecycle


def reprioritize_queue(training_data: List[List],
                       pred_data: List[np.array],
                       gpr: GaussianProcessRegressor,
                       opt_delay: float = 0.5) -> np.ndarray:
    """Determine an optimal order in which to excecute a task queue

    Args:
        database: Inputs and outputs of completed simulations
        gpr: Gaussian-process regression model
        queue: Existing task queue
        opt_delay: Minimum run time of this function
    Returns:
        Re-ordered priorities of queue
    """
    # can be called via funcx so imports
    import time
    import numpy as np
    import scipy
    import datetime

    start = datetime.datetime.now(tz=datetime.timezone.utc).timestamp()
    time.sleep(opt_delay)

    # Update the GPR with the available training data
    train_X, train_y = zip(*training_data)
    gpr.fit(np.vstack(train_X), train_y)

    # Run GPR on the existing task queue
    pred_y, pred_std = gpr.predict(pred_data, return_std=True)
    best_so_far = np.min(train_y)
    # MB: FIXED
    # ei = (best_so_far - pred_y) / pred_std
    ei = (best_so_far - pred_y) * scipy.stats.norm(0, 1).cdf((best_so_far - pred_y) / pred_std) + pred_std * scipy.stats.norm(0, 1).pdf((best_so_far - pred_y) / pred_std)

    # Argument sort the EI score, ordered with largest tasks first
    end = datetime.datetime.now(tz=datetime.timezone.utc).timestamp()
    return start, end, np.argsort(-1 * ei)


def reprioritize_fx(fx, completed, pred_data, gpr):
    store = get_store('globus')
    gpr_proxy = store.proxy(gpr)
    ft = fx.submit(reprioritize_queue, completed, pred_data, gpr_proxy)
    return ft.result()


def reprioritize(task_queue, fx, database: Dict[int, List], output_file=None):
    completed = [x[1:] for x in filter(lambda x: x[2] is not None, database.values())]
    uncompleted = [x[:2] for x in filter(lambda x: x[2] is None, database.values())]
    if len(uncompleted) > 0:
        gpr = Pipeline([('scale', MinMaxScaler(feature_range=(-1, 1))),
                        ('gpr', GaussianProcessRegressor(normalize_y=True, kernel=kernels.RBF() * kernels.ConstantKernel()))
                        ])
        # x[1] is input array
        # start_t, end_t, new_order = reprioritize_queue(completed, [x[1] for x in uncompleted], gpr=gpr)
        start_t, end_t, new_order = reprioritize_fx(fx, completed, [x[1] for x in uncompleted], gpr=gpr)

        fts = []
        priorities = []
        max_priority = len(uncompleted)
        for i, idx in enumerate(new_order):
            ft = uncompleted[idx][0]
            priority = max_priority - i
            fts.append(ft)
            priorities.append(priority)

        if output_file is not None:
            with open(output_file, 'a') as f_out:
                f_out.write(f'R START: {start_t}\n')
                f_out.write(f'R END: {end_t}\n')
                for i, ft in enumerate(fts):
                    f_out.write(f'P UPDATE: {ft.eq_task_id} {ft.priority} {priorities[i]}\n')

        task_queue.update_priorities(fts, priorities)


def submit_initial_tasks(task_queue, exp_id, params: Dict):
    search_space_size = params['search_space_size']
    dim = params['sample_dimensions']
    sampled_space = np.random.uniform(size=(search_space_size, dim), low=-32.768, high=32.768)

    task_type = 0
    mean_rt = params['runtime']
    std_rt = params['runtime_var']

    payloads = []
    for sample in sampled_space:
        payload = json.dumps({'x': list(sample), 'mean_rt': mean_rt, 'std_rt': std_rt})
        payloads.append(payload)
    fts = task_queue.submit_tasks(exp_id, eq_type=task_type, payload=payloads)

    database = {}
    for i, ft in enumerate(fts):
        database[ft.eq_task_id] = [ft, sampled_space[i], None]

    return database


def run(exp_id, params: Dict):
    output_file = f'./output/{exp_id}_output.txt'
    # To avoid errors in finally
    task_queues = pools = dbs = fx_executors = {}
    try:
        fx_endpoints, db_names, pool_names = lifecycle.find_active_elements(params)
        repro_endpoint = params['reprioritize_endpoint']
        if repro_endpoint not in fx_endpoints:
            fx_endpoints.append(repro_endpoint)

        fx_executors = lifecycle.initialize_fx_endpoints(fx_endpoints, params)
        dbs = lifecycle.initialize_dbs(db_names, fx_executors, params)
        task_queues = lifecycle.initialize_task_queues(fx_executors, dbs, params)
        task_queue = task_queues['sim']
        database = submit_initial_tasks(task_queue, exp_id, params)
        # launch after submitting so pool has full data
        pools = lifecycle.initialize_worker_pools(exp_id, pool_names, fx_executors,
                                                  dbs, params)
        lifecycle.initialize_proxystore(params)

        num_guesses = params['num_guesses']
        retrain_after = params['retrain_after']
        # next_retrain = retrain_after
        tasks_completed = 0
        fts = [v[0] for _, v in database.items()]
        print(f'NUM GUESSES: {num_guesses}')
        print(f'RETRAIN AFTER: {retrain_after}')
        print(f'FTS: {len(fts)}')
        num_repro = 0
        while tasks_completed < num_guesses:
            completed_fts = task_queue.pop_completed(fts, n=retrain_after)
            for ft in completed_fts:
                _, result = ft.result()
                database[ft.eq_task_id][2] = float(result)
                tasks_completed += 1

            print(f"tasks completed: {tasks_completed}")
            reprioritize(task_queue, fx_executors[repro_endpoint], database, output_file=output_file)
            num_repro += 1
            if num_repro == 2:
                # pool_names = 'bebop2', add 'bebop2' to params with params['tasks'][0]['pools'].append()
                params['tasks'][0]['pools'].append('bebop2')
                p = lifecycle.initialize_worker_pools(exp_id, ['bebop2'], fx_executors,
                                                      dbs, params)
                pools.update(p)
                print(pools)
            elif num_repro == 4:
                params['tasks'][0]['pools'].append('bebop3')
                p = lifecycle.initialize_worker_pools(exp_id, ['bebop3'], fx_executors,
                                                      dbs, params)
                pools.update(p)

    finally:
        for task_queue in task_queues.values():
            task_queue.shutdown()
        for db in dbs.values():
            db.shutdown()
        for pool in pools.values():
            pool.shutdown()
        for fx in fx_executors.values():
            fx.shutdown()


def create_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('exp_id', help='experiment id')
    parser.add_argument('config_file', help="yaml format configuration file")
    return parser


if __name__ == '__main__':
    parser = create_parser()
    args = parser.parse_args()
    with open(args.config_file) as fin:
        params = yaml.safe_load(fin)

    # launch.launch_dbs(params)
    # launch.launch_worker_pools(args.exp_id, params)
    # launch.stop_dbs(params)

    run(args.exp_id, params)
