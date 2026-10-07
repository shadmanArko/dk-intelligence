-- Phase 2.1: the reach question. For every measured post: did it reach more people than the median latest reach of
-- the previous 20 posts on the same platform? (same definition as datasets/content.py reach_label)
alter table post_outcome add column reach_median double precision;
alter table post_outcome add column label_reach smallint check (label_reach in (0, 1));
