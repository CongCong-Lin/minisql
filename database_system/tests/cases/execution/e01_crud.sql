CREATE TABLE student(id INT, name VARCHAR);
INSERT INTO student(id, name) VALUES(1, 'Ada');
INSERT INTO student(name, id) VALUES('Bob', 2);
SELECT * FROM student;
SELECT name FROM student WHERE id = 2;
DELETE FROM student WHERE id = 1;
SELECT * FROM student;
