SELECT s.name,c.title,s.score FROM student s JOIN course c ON s.id=c.student_id ORDER BY s.id,c.title;
