CREATE TABLE t(id INT, score INT);
INSERT INTO t(id,score) VALUES(1,80);
INSERT INTO t(id,score) VALUES(2,90);
UPDATE t SET score=1/(id-2);
SELECT score FROM t ORDER BY id;
