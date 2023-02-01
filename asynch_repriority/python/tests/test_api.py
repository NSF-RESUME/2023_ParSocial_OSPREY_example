import unittest
import yaml
import lifecycle


class LifecycleTests(unittest.TestCase):

    def test_fx_endpoints(self):
        cfg = """
        queues:
          bebop:
            db: bebop
            type: api_server
        dbs:
            bebop:
              type: funcx
              fx_endpoint: bebop

        tasks:
          - type: 0
            pool: bebop1
            db: bebop

        pools:
          bebop1:
            type: funcx
            fx_endpoint: foo

        fx_endpoints:
            bebop: 422f
            foo: 65b
        """

        params = yaml.safe_load(cfg)
        ae = lifecycle.find_active_elements(params)
        self.assertEqual(ae.fx_endpoints, ['foo', 'bebop'])
        self.assertEqual(ae.dbs, ['bebop'])
        self.assertEqual(ae.pools, ['bebop1'])
