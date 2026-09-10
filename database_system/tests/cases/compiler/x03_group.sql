CREATE TABLE t(id INT);
SELECT a.id,COUNT(*) AS n FROM t a JOIN t b ON a.id=b.id GROUP BY a.id HAVING n>0 ORDER BY n;
