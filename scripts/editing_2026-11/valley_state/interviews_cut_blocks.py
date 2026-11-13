"""Answer blocks for every interview in the full timeline (source seconds of the
main file), read from the transcripts on 2026-11-11. Everything outside a block
(questions, crew talk, slates, sound checks) is left out; inside a block only ums
and stalls are removed. Flags for the editor are in FLAGS."""
BLOCKS = {
  "C0523 ii.MP4": [  # Joe Wilson, Shriners
    (47.96, 52.66), (58.82, 69.00), (73.26, 148.60), (155.34, 179.78), (186.56, 197.62),
    (244.28, 356.72), (372.54, 413.54), (423.12, 502.88), (513.34, 585.38), (593.80, 618.86)],
  "C0525 II.MP4": [  # Jeffrey Burke
    (75.44, 83.48), (87.90, 257.56), (263.76, 395.50), (403.78, 456.50), (462.62, 496.60)],
  "A7Siii0491.MP4": [  # Norman Williams
    (31.68, 119.30), (136.06, 287.76), (297.62, 413.28), (432.12, 587.52), (606.46, 779.24)],
  "C0519 ii.MP4": [  # Daniel Henson (ID number 26.9-29.1 left out)
    (23.90, 26.70), (29.10, 322.80), (328.30, 350.20), (366.70, 438.20)],
  "C0520 ii.MP4": [  # Noah Duenas
    (6.00, 7.20), (8.70, 9.80), (26.80, 111.80), (112.70, 185.20), (208.10, 208.40),
    (211.30, 278.20), (299.70, 404.70)],
  "C0521 ii.MP4": [  # Lester Polk
    (35.20, 81.00), (104.00, 149.70), (196.00, 201.20), (217.50, 254.70), (299.10, 318.40)],
  "C0522 ii.MP4": [  # on death row since 1981
    (11.00, 83.00), (95.70, 224.00), (229.10, 322.60), (327.10, 388.80), (397.20, 608.00),
    (621.50, 707.90)],
  "A7Siii0506.MP4": [  # Sean Diaz Garcia Jr. statement
    (45.74, 128.40)],
}
DROP = {"A7Siii0507.MP4", "C0544.MP4"}   # sound check and camera setup, no interview
REUSE_ELI = "Valley State Prison Mini Doc - Eli Full v7"   # A7Siii0470 + A7Siii0508 pieces
WAV = {"C0523 ii.MP4": "C0523ii", "C0525 II.MP4": "C0525II", "A7Siii0491.MP4": "A7Siii0491",
       "C0519 ii.MP4": "C0519ii", "C0520 ii.MP4": "C0520ii", "C0521 ii.MP4": "C0521ii",
       "C0522 ii.MP4": "C0522ii", "A7Siii0506.MP4": "A7Siii0506"}
FLAGS = [
  "Daniel: his prison ID number (C0519 ii 26.9-29.1) left out.",
  "Lester: he gave '33 and a half years, no write-ups' twice; the interviewer asked him to say it again, so only the retake (196.0) is kept.",
  "Norman: his first name take (16.8-24.5) left out; the second take starts the answer.",
  "Lester's slate ('Lester Polk Lester Polk') and Joe's name spelling are treated as slate; Joe's spelling is kept as the intro, cut if you like.",
]
