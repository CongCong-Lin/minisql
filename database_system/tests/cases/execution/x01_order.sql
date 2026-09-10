CREATE TABLE t(id INT, name VARCHAR);
INSERT INTO t(id,name) VALUES(1,'张三');
INSERT INTO t(id,name) VALUES(2,'李四');
SELECT name AS label FROM t ORDER BY id DESC;
