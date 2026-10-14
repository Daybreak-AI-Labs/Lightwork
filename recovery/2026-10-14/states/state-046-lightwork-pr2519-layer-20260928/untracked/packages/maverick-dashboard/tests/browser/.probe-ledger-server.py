import server

server.CONFIG = server.CONFIG.replace("[features]\n", "[features]\ncopilot_operations = true\n", 1)

original = server.build_app


def build(directory):
    app = original(directory)
    from maverick.paths import data_dir, tenant_scope
    from maverick.self_improvement import PromotionLedger, PromotionRecord
    with tenant_scope(tenant="browser-workspace"):
        ledger = PromotionLedger(path=data_dir("self_improvement.json"))
        ledger.add(PromotionRecord(id="synthetic-private-record", rung="workflow",
            summary="Synthetic private summary", baseline_score=0, candidate_score=0,
            promoted_at=1700000000))
    return app


server.build_app = build
server.main()
