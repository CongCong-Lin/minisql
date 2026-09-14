SELECT id FROM student a JOIN student b ON a.id=b.id;
UPDATE student SET score=10/(id-2);
SELECT id,score FROM student ORDER BY id;
