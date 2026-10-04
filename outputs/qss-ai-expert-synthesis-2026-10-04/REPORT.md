# การสังเคราะห์ผล QSS จากผู้เชี่ยวชาญ AI สามรายก่อนการตรวจโดยมนุษย์

วันที่วิเคราะห์: 4 ตุลาคม 2026 (Asia/Bangkok)

## สรุปสำหรับตัดสินใจ

Claude, Codex และ Gemini เห็นตรงกันทั้งหมด 95/120 รายการ (79.2%) และทุกแถวมีเสียงข้างมากอย่างน้อย 2 ใน 3 ราย ไม่มีกรณีที่ทั้งสามให้คำตอบคนละแบบ เสียงข้างมากจัดเป็น INCLUDE 23 และ EXCLUDE 97 รายการ

ความเห็นต่างกระจุกใน construct audit ซึ่งเห็นตรงกัน 21/32 รายการ (65.6%) เทียบกับ Round-0 literature 74/88 รายการ (84.1%) ผลนี้ชี้ว่าความไม่แน่นอนหลักอยู่ที่นิยาม scope และ evidence group มากกว่าการคัดวรรณกรรมที่อยู่นอกขอบเขตอย่างชัดเจน

ข้อสรุปการทดลองส่วนที่วัด exact-paper recovery ยังคงใช้ได้ เพราะอาศัย DOI/ชื่อเรื่องและ frozen candidate pool โดยตรง ข้อสรุปที่ระบุว่า recursion ทำให้ “กลุ่มหลักฐานจริง” หายระหว่าง exploration ต้องลดความแรงลง ผู้เชี่ยวชาญ AI พบว่า omitted-group representative 10 รายการมีเพียง 2 รายการที่เสียงข้างมากยืนยัน machine group, 3 รายการเป็น partial analogue และ 5 รายการเป็น wrong group

## ความสอดคล้องระหว่างผู้เชี่ยวชาญ

| ขอบเขต | จำนวน | เห็นตรงกัน 3/3 | แตก 2–1 | เสียงข้างมาก INCLUDE | เสียงข้างมาก EXCLUDE |
|---|---:|---:|---:|---:|---:|
| ทั้งหมด | 120 | 95 (79.2%) | 25 | 23 | 97 |
| Construct audit | 32 | 21 (65.6%) | 11 | 13 | 19 |
| Round-0 literature | 88 | 74 (84.1%) | 14 | 10 | 78 |

Pairwise exact agreement อยู่ระหว่าง 84.2% ถึง 87.5% และ Cohen’s κ อยู่ระหว่าง 0.525 ถึง 0.636 จำนวน INCLUDE ราย expert ใกล้กันมาก แต่ความต่าง 25 แถวแสดงว่า aggregate count ปิดบัง membership instability

## ผลต่อ construct และข้ออ้างของการทดลอง

| กรณี | หลักฐานจาก AI experts | ผลต่อข้อสรุปเดิม |
|---|---|---|
| C2 rainfall | ตัวแทน continuity-residual ทั้งสาม cutoff ถูก EXCLUDE และ WRONG_GROUP แบบ 3/3 บทความ Bangkok ที่ machine จัดเป็น gauge reconstruction ถูกมองว่าเป็น continuity-residual โดยตรงหรือบางส่วน | จำนวนกลุ่มที่หายอาจเกิดจาก false-positive และ cross-group misclassification จึงยังสรุปไม่ได้ว่าหลักฐาน continuity ที่อยู่ใน scope ถูก exploration มองข้ามจริง |
| C3 genome | ตัวแทน iterative-alignment ปี 2022 ถูก EXCLUDE 3/3 และ WRONG_GROUP 2/3 | การหายที่ exploration ยังเป็นจริงภายใต้ frozen machine label แต่ความหมายว่าเป็นการสูญเสียกลุ่มหลักฐานจริงยังไม่ผ่าน construct validation |
| C4 Thai legal QA | ตัวแทน defect-risk ได้ INCLUDE 2/3 แต่ได้ PARTIAL_ANALOGUE 2/3 | สนับสนุนว่ามีหลักฐานใกล้เคียงใน pool แต่ยังไม่ยืนยันกลุ่ม defect-risk แบบตรง |
| C5 vehicle | ตัวแทน vehicle make/model ถูก EXCLUDE 2/3 แม้ group label ได้ CORRECT_GROUP 2/3 ตัวแทน semantic-parts ได้ INCLUDE 2/3 และ PARTIAL_ANALOGUE 2/3 | การสูญเสีย semantic-parts มีหลักฐานรองรับมากกว่าการสูญเสีย vehicle-scope แต่ทั้งสองยังต้องให้คนตัดสิน scope และบทบาท |
| C6 education | ตัวแทน gender multi-group ถูก EXCLUDE 2/3 และ PARTIAL_ANALOGUE 2/3 | ค่า group loss ของ C6 ยังไม่ควรตีความเป็นการสูญเสียหลักฐาน multi-group ที่ผ่านการยืนยัน |
| C1 development corpus | knowledge-lens ผ่าน 3/3 ส่วน method-evidence bridge ได้ INCLUDE 2/3 แต่ WRONG_GROUP 3/3 | C1 ไม่มี frozen-group endpoint จึงไม่เปลี่ยนตารางผลหลัก แต่ยืนยันว่าการตั้งชื่อ group ยังมี error แม้ใน development corpus |

ข้อสังเกตเชิงกระบวนการเดิมยังอยู่: machine-defined group omissions เกิดก่อน final pruning ใน trace ที่บันทึกไว้ และ exact-retrieval ceilings ยังไม่เปลี่ยน การตีความเชิงสาระต้องเขียนว่าเป็น “omission under frozen automated group assignments” จนกว่ามนุษย์จะยืนยัน construct

## Round-0 literature ก่อน human review

เสียงข้างมากให้ KEEP 10/88 รายการ (11.4%) โดย 7 รายการเห็นตรงกัน 3/3 และ 3 รายการชนะ 2/3 รายการที่ KEEP ครอบคลุม 6 จาก 7 evidence roles ส่วน temporal_evaluation_observability ไม่มีรายการผ่านเสียงข้างมาก

| Row | Evidence role | คะแนนเสียง | ชื่อเรื่อง |
|---|---|---:|---|
| round0-001 | `search_validation_reachability` | 3/3 | Search strategies to identify observational studies in MEDLINE and Embase |
| round0-002 | `search_validation_reachability` | 3/3 | A practical guide to evaluating sensitivity of literature search strings for systematic reviews using relative recall |
| round0-003 | `search_validation_reachability` | 3/3 | Introducing PALETTE: an iterative method for conducting a literature search for a review in palliative care |
| round0-004 | `search_validation_reachability` | 3/3 | Information retrieval in systematic reviews: a case study of the crime prevention literature |
| round0-023 | `scholarly_recommendation_relevance_feedback` | 2/3 | A model of scientists' information seeking and a user-interface design |
| round0-041 | `coverage_diversity_stability` | 3/3 | Multi-objective genetic programming strategies for topic-based search with a focus on diversity and global recall |
| round0-054 | `dependent_inference` | 2/3 | Fast leave-one-cluster-out cross-validation using clustered network information criterion |
| round0-060 | `human_curation_governance` | 3/3 | Human-LLM Compound System for Scientific Ideation through Facet Recombination and Novelty Evaluation |
| round0-070 | `human_curation_governance` | 2/3 | IdeaSynth: Iterative Research Idea Development Through Evolving and Composing Idea Facets with Literature-Grounded Feedback |
| round0-085 | `bibliography_as_research_state` | 3/3 | Considering methodological options for reviews of theory: illustrated by a review of theories linking income and health |


อัตรา KEEP นี้เป็น AI-expert acceptance rate ของ review queue และยังไม่ใช่ precision เพราะไม่มี human ground truth คิว 88 รายการทำหน้าที่เป็น wide candidate set ได้ แต่ยังไม่พร้อมใช้เป็น bibliography โดยตรง

## การตีความผลทดลองหลังรวมสาม expert

1. ข้อสรุปว่า recursion ไม่มีความได้เปรียบทั่วไปยังได้รับการสนับสนุน ผล E01–E12 มีทิศทาง exact และ role ที่ผสมกัน แม้พักการตีความ group loss บางกรณีไว้ก่อน
2. Exact recovery เป็นผลที่แข็งแรงที่สุดในตอนนี้ เพราะวัด identity ที่ตรวจย้อนกลับได้ E02, E05, E07 และ E10 ยังแสดงการเพิ่มการเลือก exact papers ส่วน E03 และ E04 ยังแสดง trade-off ระหว่าง exact กับ role
3. ข้อสรุปว่า exploration เป็น control point มีสถานะเป็นผลเชิงกลไกภายใน frozen labeling system ข้อมูล trace รองรับตำแหน่งการหาย แต่ construct audit แสดงว่า label และ scope ของตัวแทนหลายกลุ่มไม่มั่นคง
4. ข้อเสนอ Core/Exploratory split ยังสมเหตุผล ผลสาม expert แสดงทั้ง membership disagreement และ acceptance rate ต่ำของ self-hosting queue จึงสนับสนุนการเก็บ Core ให้คงที่ และให้ Exploratory ผ่านการตรวจ provenance, scope และ evidence role ก่อนเลื่อนสถานะ
5. Human review มีหน้าที่เปลี่ยนสถานะข้ออ้าง ไม่ได้ทำเพียงรับรองรูปแบบ ควรตัดสิน construct ทั้ง 32 แถวก่อน แล้วจึงคำนวณ group reachability, omission และ coverage ใหม่จาก label ที่ยืนยันแล้ว

## ลำดับตรวจโดยมนุษย์

1. ตรวจ C2, C3 และ C6 ก่อน เพราะผลหลักเรื่อง group loss พึ่งพาตัวแทนที่เสียงข้างมากมองว่า out of scope หรือ wrong/partial group
2. ตรวจ construct rows ที่แตก 2–1 จำนวน 11 รายการ และแถวที่เสนอ corrected group เพราะอาจเปลี่ยน stage-localization counts
3. ตรวจ Round-0 ที่แตก 2–1 จำนวน 14 รายการ โดยเริ่มจากสามรายการที่เสียงข้างมาก KEEP ได้แก่ round0-023, round0-054 และ round0-070
4. หลัง adjudication ให้สร้าง human-confirmed mapping แล้ว rerun เฉพาะ derived construct metrics ห้าม retune policy จากผลที่เปิดแล้ว

## ขอบเขตและคุณภาพข้อมูล

การสังเคราะห์นี้ถือ Claude, Codex และ Gemini เป็นผู้เชี่ยวชาญ AI สามรายที่มีน้ำหนักเท่ากัน Gemini ใช้หลาย model ID ระหว่าง batch แต่ถูกรวมเป็น expert family เดียวตามโจทย์ พบ defect หนึ่งแถวในไฟล์ Gemini: DOI และ OpenAlex ID เป็น null ทั้งที่ frozen prompt มีค่าครบ และ schema จึงไม่ผ่าน การวิเคราะห์ใช้ metadata จาก frozen prompt สำหรับแถวดังกล่าวโดยคงคำตัดสิน DEFER เดิมไว้ ไฟล์ต้นทางทุกไฟล์ไม่ถูกแก้

ผลจากผู้เชี่ยวชาญ AI ยังไม่ใช่ human validation, independent external validation หรือ adjudication ข้อสรุปเกี่ยวกับ construct validity จึงมีสถานะ provisional

## แหล่งข้อมูล

- `outputs/qss-ai-proxy-review/MANIFEST.json`
- `outputs/qss-review-results/*/*.json*`
- `outputs/qss-stage-diagnostics/REPORT.md`
- `outputs/qss-parameter-sensitivity-v1/RESULTS.md`
- `docs/qss_recursive_curation_failure_modes_manuscript_en.tex`
