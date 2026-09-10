CREATE TABLE t(team VARCHAR, score INT);
INSERT INTO t(team,score) VALUES('A',80);
INSERT INTO t(team,score) VALUES('A',90);
INSERT INTO t(team,score) VALUES('B',60);
SELECT team,COUNT(*) AS n,SUM(score) AS total,AVG(score) AS mean,MIN(score),MAX(score) FROM t GROUP BY team HAVING mean>70.5 ORDER BY total DESC;
