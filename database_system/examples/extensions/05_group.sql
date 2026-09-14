SELECT s.team,COUNT(*) AS n,SUM(s.score) AS total,AVG(s.score) AS mean
FROM student s JOIN course c ON s.id=c.student_id
GROUP BY s.team HAVING AVG(s.score)>70 ORDER BY total DESC;
