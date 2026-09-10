CREATE TABLE t(a INT, b INT, c INT);
INSERT INTO t(a, b, c) VALUES(1, 2, 3);
SELECT * FROM t WHERE a = 1 OR b = 2 AND c = 0;
