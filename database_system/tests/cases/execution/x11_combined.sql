CREATE TABLE t(id INT, score INT);
INSERT INTO t(id,score) VALUES(1,80);
INSERT INTO t(id,score) VALUES(2,90);
UPDATE t SET score=score+1;
SELECT a.id,AVG(b.score) AS mean FROM t a INNER JOIN t b ON a.id=b.id GROUP BY a.id HAVING mean>0 ORDER BY mean DESC;
