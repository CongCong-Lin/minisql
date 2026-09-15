-- 第一块：建立包含五种类型的表，姓名不允许为空。
CREATE TABLE showcase(id INT, name VARCHAR NOT NULL, score FLOAT, active BOOL, birthday DATE);

-- 第二块：显式列清单插入数据，展示日期、布尔值和空值。
INSERT INTO showcase(id,name,score,active,birthday) VALUES(1,'小明',92.5,TRUE,DATE '2003-05-12');
INSERT INTO showcase(id,name,score,active,birthday) VALUES(2,'小红',88,FALSE,DATE '2004-02-29');
INSERT INTO showcase(id,name,score,active,birthday) VALUES(3,'小林',NULL,TRUE,NULL);
INSERT INTO showcase(id,name,score,active,birthday) VALUES(4,'小周',76.5,NULL,DATE '2003-09-01');
INSERT INTO showcase(id,name,score,active,birthday) VALUES(5,'小王',95.0,TRUE,DATE '2002-12-31');
INSERT INTO showcase(id,name,score,active,birthday) VALUES(6,'小陈',NULL,FALSE,NULL);

-- 第三块：空值判断、分组和聚合，聚合忽略空值。
SELECT name FROM showcase WHERE score IS NULL ORDER BY id;
SELECT active,COUNT(*) AS people,COUNT(score) AS scored,AVG(score) AS average FROM showcase GROUP BY active;
SELECT name,score,birthday FROM showcase ORDER BY score DESC,id ASC;

-- 第四块：创建持久化索引并采集统计，小表选择顺序扫描也是合理结果。
CREATE INDEX idx_showcase_id ON showcase(id);
ANALYZE showcase;
EXPLAIN SELECT * FROM showcase WHERE id=3;
EXPLAIN FORMAT JSON UPDATE showcase SET score=100 WHERE id=1;

-- 第五块：事务内可以看到修改，回滚后恢复原值。
BEGIN;
UPDATE showcase SET score=60 WHERE id=1;
SELECT id,score FROM showcase WHERE id=1;
ROLLBACK;
SELECT id,score FROM showcase WHERE id=1;

-- 第六块：提交后重新连接仍可读取，显式只读事务允许查询。
BEGIN;
UPDATE showcase SET score=99 WHERE id=1;
COMMIT;
BEGIN READ ONLY;
SELECT * FROM showcase ORDER BY id;
COMMIT;

-- 第七块：删除索引也参与事务，此处撤销删除，保留索引用于界面展示。
BEGIN;
DROP INDEX idx_showcase_id;
ROLLBACK;
