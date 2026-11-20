"""Run large joint verification jobs with private geometry and DRC runtimes."""
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import multiprocessing

from .resources import cancel_check, cancellation_scope, serial_scope

_worker = None
spawn_enabled = False


def enable_spawn():
    """Opt in from an application entry point guarded by __main__."""
    global spawn_enabled
    spawn_enabled = True


def _initialize(data, case, roles, order, cache, parameters, settings, plans, active, grid, bundles, stop):
    global _worker
    from .contact import ContactModel
    from .reconstruction import Reconstructor
    from .stack import Connections

    with serial_scope():
        connections = Connections(data, roles, order, cache.geometries)
        model = ContactModel(data, case, roles, connections, cache, parameters=parameters)
        solver = Reconstructor(model, parameters, settings, plan_pool=plans)
        solver._plans = active
    _worker = solver, grid, bundles, stop


def _verify(digits):
    from .reconstruction_joint import scene
    from .width_keepout import routing_violations

    solver, grid, bundles, stop = _worker
    with cancellation_scope(stop), serial_scope():
        cancel_check()
        states, frame = scene(solver, grid, bundles, digits)
        edges = routing_violations(solver.model, states, solver.p, solver.settings, frame['replacements'])
        fields = ('layer', 'index', 'die', 'route_identity', 'connection_failed',
                  'missing_region', 'rule_witnesses', 'problem_reasons')
        pieces = []
        for q in frame['pieces']:
            item = {key: q[key] for key in fields if key in q}
            item['regions'] = {'A': q['regions']['A']}
            if 'nominal_regions' in q:
                item['nominal_regions'] = {'A': q['nominal_regions']['A']}
            pieces.append(item)
        summary = dict(pieces=pieces, repair_refs=frame['repair_refs'], hard_failures=frame['hard_failures'], violations=bool(frame['violations']))
        return digits, states, summary, edges


def isolated_scenes(r, grid, bundles, visits, workers, idle):
    """Submit bounded jobs and return each real completion independently."""
    ctx = multiprocessing.get_context('spawn')
    stop = ctx.Event()
    model = r.model
    inputs = (model.data, model.case, model.roles, model.connections.order, model.cache, r.p, r.settings,
              r.plan_pool, r._plans, grid, bundles, stop)
    queue = iter(visits)
    pending = set()
    executor = ProcessPoolExecutor(workers, mp_context=ctx, initializer=_initialize, initargs=inputs)

    def submit():
        try:
            pending.add(executor.submit(_verify, next(queue)))
        except StopIteration:
            pass

    try:
        for _ in range(workers * 2):
            submit()
        while pending:
            cancel_check()
            ready, _ = wait(pending, timeout=.5, return_when=FIRST_COMPLETED)
            if not ready:
                idle()
            for future in ready:
                pending.remove(future)
                result = future.result()
                submit()
                yield result
    finally:
        stop.set()
        for future in pending:
            future.cancel()
        if pending and wait(pending, timeout=1).not_done:
            terminate = getattr(executor, 'terminate_workers', None)
            if terminate is not None:
                terminate()
            else:
                for child in tuple((executor._processes or {}).values()):
                    if child.is_alive():
                        child.terminate()
        executor.shutdown(wait=True, cancel_futures=True)
