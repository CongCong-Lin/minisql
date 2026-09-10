CREATE TABLE t(id INT);
INSERT INTO t(id) VALUES(1);
INSERT INTO t(id) VALUES(1);
SELECT a.id,b.id FROM t a JOIN t b ON a.id=b.id ORDER BY a.id;
