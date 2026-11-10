# Agent: scheduler
# Description: Ensure proper schedule coverage for each store according taking into account many factors including size, weather, staff time-off, special events, FT/PT 
Owns: orchestration graph and the OR-Tools solver.
Never computes legality itself — delegates all legality questions to `labor_compliance`.


