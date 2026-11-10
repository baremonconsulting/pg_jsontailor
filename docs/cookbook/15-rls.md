# 15. RLS and visibility

Generated views are security invokers. Test RLS on every source relation as the real application role, with point reads, arrays, writes and deletes for two tenants. Deny direct source-table access that would bypass document ETags.

