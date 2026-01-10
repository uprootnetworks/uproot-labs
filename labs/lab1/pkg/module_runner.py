

def run(args):
    if args.default:
        from pkg.rollback import rollback_all
        rollback_all()

    if args.all:
        from pkg.routers import run_sp_routers
        run_sp_routers()
        from pkg.firewalls import run_firewalls
        run_firewalls()
        from pkg.switch import run_switch
        run_switch()

    if args.switch:
        from pkg.switch import run_switch
        run_switch()

    if args.router:
        from pkg.routers import run_sp_routers
        run_sp_routers()

    if args.firewall:
        from pkg.firewalls import run_firewalls
        run_firewalls()
