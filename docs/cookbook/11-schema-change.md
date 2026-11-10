# 11. Schema change and regeneration

Stop writes, review the relational migration, regenerate and diff DDL, apply atomically, restore capabilities, then run `__check()`, smoke tests and restore rehearsal. Views from earlier development snapshots must be regenerated after the 0.2.0 generator correction.

