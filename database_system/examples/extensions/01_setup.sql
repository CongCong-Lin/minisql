CREATE TABLE student(id INT, name VARCHAR, score INT, team VARCHAR);
CREATE TABLE course(student_id INT, title VARCHAR);
INSERT INTO student(id,name,score,team) VALUES(1,'张三',85,'A');
INSERT INTO student(id,name,score,team) VALUES(2,'李四',92,'A');
INSERT INTO student(id,name,score,team) VALUES(3,'王五',67,'B');
INSERT INTO course(student_id,title) VALUES(1,'数据库');
INSERT INTO course(student_id,title) VALUES(1,'编译原理');
INSERT INTO course(student_id,title) VALUES(2,'数据库');
INSERT INTO course(student_id,title) VALUES(3,'数据库');
