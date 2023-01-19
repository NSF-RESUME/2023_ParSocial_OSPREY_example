import numpy as np
import time
import json


def run(payload: str) -> str:
    args = json.loads(payload)
    x = np.array(args['x'])
    mean_rt = args['mean_rt']
    std_rt = args['std_rt']

    result = ackley(x, mean_rt=mean_rt, std_rt=std_rt)
    # print(f'Result: {result}', flush=True)
    return json.dumps(result)

def ackley(x: np.ndarray, a=20, b=0.2, c=2 * np.pi, mean_rt=0, std_rt=0.1) -> np.float64:
    """The Ackley function (http://www.sfu.ca/~ssurjano/ackley.html)
    Args:
        x (ndarray): Points to be evaluated. Can be a single or list of points
        a (float): Parameter of the Ackley function
        b (float): Parameter of the Ackley function
        c (float): Parameter of the Ackley function
        mean_rt (float): ln(Mean runtime in seconds)
        std_rt (float): ln(Standard deviation of runtime in seconds)
    Returns:
        y (ndarray): Output of the Ackley function
    """

    # Simulate this actually taking awhile
    runtime = np.random.lognormal(mean_rt, std_rt)
    time.sleep(runtime)

    # Make x an array
    x = np.array(x)

    # Get the dimensionality of the problem
    if x.ndim == 0:
        x = x[None, None]
    elif x.ndim == 1:
        x = x[None, :]
    d = x.shape[1]
    y = - a * np.exp(-b * np.sqrt(np.sum(x ** 2, axis=1) / d)) - np.exp(np.cos(c * x).sum(axis=1) / d) + a + np.e
    return y[0]