# Synthetic data generator

**Spec:** §4 Dataset · **Owner:** @participant1

**Acceptance condition:** running the generator with a fixed seed reproduces `data/raw/` exactly,
yields ≥ 10,000 transactional/event records, keeps values internally consistent (keys resolve,
timelines are plausible) and injects each defect type at a documented, countable rate so detection
can be verified in `docs/04-quality-evidence/`.
