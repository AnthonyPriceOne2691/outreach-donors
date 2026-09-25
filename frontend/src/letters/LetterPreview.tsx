/**
 * Одно письмо: текст, процент отличия и три решения — отправить,
 * поправить, не писать.
 *
 * **Текст виден целиком, а не в виде сводки.** Смысл экрана в том, что
 * спорное решение видит человек; пересказ письма вместо письма этот
 * смысл отменяет.
 *
 * **Процент стоит рядом с текстом, а не в списке.** Число само по себе
 * ничего не говорит — говорит число рядом с текстом, к которому оно
 * относится, и вердикт словами рядом с числом.
 *
 * **Правка идёт в том же месте, где чтение.** Отдельное окно правки
 * прячет то, ради чего окно и открыли.
 *
 * **Добивки читаются здесь же, вкладками.** Согласуя первое письмо,
 * человек согласует всю цепочку: следующие два уйдут сами, по сроку.
 * Узнать о втором письме от донора — худший способ увидеть его текст.
 * Править их нельзя: текст добивки один на всю рассылку и живёт
 * шаблоном в коде, иначе у каждого письма заведётся своя правда.
 *
 * **Незаданное — тихой пометкой везде: в первом письме, в добивках и в
 * правке** (`letterText.ts`). Громкая метка сервера на экран не попадает.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Stack,
  Tabs,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useEffect, useRef, useState } from 'react';

import type { Corridor, QueuedLetter } from '../api/types';
import { readable, restored, uniquenessText } from './letterText';

export interface LetterPreviewProps {
  letter: QueuedLetter;
  corridor: Corridor;
  /** Чем заблокирована отправка. Непусто — кнопка не нажимается. */
  blockedBy: string[];
  /** Уходит ли письмо на самом деле. */
  transportIsReal: boolean;
  canSend: boolean;
  busy: boolean;
  onSend: () => void;
  onSkip: () => void;
  onSave: (subject: string, body: string) => void;
}

/** Зелёный — в коридоре, янтарь — нет. Те же два смысла, что и везде:
 *  «готово» и «нужно внимание». Третьего оттенка тут не бывает. */
export function toneOf(letter: QueuedLetter): 'green' | 'yellow' {
  return letter.verdict === null ? 'green' : 'yellow';
}

/** Пояснение к пометкам в скобках — под любым текстом, где они есть. */
function UnsetHint({ editing = false }: { editing?: boolean }) {
  return (
    <Text size="xs" c="dimmed">
      {editing
        ? 'В квадратных скобках — то, что подставится при подключении почты: оставьте как есть или впишите значение.'
        : 'В квадратных скобках — то, что подставится при подключении почты.'}
    </Text>
  );
}

export function LetterPreview({
  letter,
  corridor,
  blockedBy,
  transportIsReal,
  canSend,
  busy,
  onSend,
  onSkip,
  onSave,
}: LetterPreviewProps) {
  const [editing, setEditing] = useState(false);
  // Правят то же, что читают: с тихими пометками вместо громких меток.
  const [subject, setSubject] = useState(() => readable(letter.subject).text);
  const [body, setBody] = useState(() => readable(letter.body).text);
  const [tab, setTab] = useState<string>('first');
  const card = useRef<HTMLDivElement>(null);

  // Смена письма сбрасывает правку: иначе текст одного донора уехал бы
  // в письмо другому — а это ровно та ошибка, которую уже не отозвать.
  useEffect(() => {
    setEditing(false);
    setSubject(readable(letter.subject).text);
    setBody(readable(letter.body).text);
    setTab('first');
  }, [letter.id, letter.subject, letter.body]);

  // Закреплённая карточка прокручивается внутри себя, и новое письмо
  // открывалось бы с середины — там, где дочитали прежнее.
  useEffect(() => {
    if (card.current !== null) card.current.scrollTop = 0;
  }, [letter.id]);

  const followup = letter.followups.find((step) => `step${step.step}` === tab) ?? null;

  const blocked = blockedBy.length > 0;
  const shown = readable(letter.body);
  const title = readable(letter.subject);
  const next = followup === null ? null : readable(followup.body);

  return (
    <Card className="glass letterPreview scrollSlim" p="xl" ref={card}>
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Stack gap={4} style={{ minWidth: 0 }}>
            <Text fw={600} style={{ overflowWrap: 'anywhere' }}>
              {letter.host}
            </Text>
            <Text size="xs" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
              {letter.email ?? 'адрес не определён'} · кампания «{letter.campaign}»
            </Text>
          </Stack>
          {/* Не ужимается: на узком окне значок сжимался до «отли…»,
              то есть до слова, которое ничего не значит. */}
          <Badge variant="light" color={toneOf(letter)} style={{ flexShrink: 0 }}>
            отличие {uniquenessText(letter.uniqueness, corridor)}
          </Badge>
        </Group>

        {/* Коридор назван один раз — в самом вердикте сервера: «Коридор —
            15–25%» следом за «…выше коридора 15–25%» читался повтором. */}
        {letter.verdict !== null ? (
          <Alert color="yellow" title="Отличие вне коридора">
            {letter.verdict}. Письмо можно отправить и таким, но лучше поправить.
          </Alert>
        ) : null}

        <Tabs value={tab} onChange={(value) => setTab(value ?? 'first')} variant="pills">
          <Tabs.List>
            <Tabs.Tab value="first">Первое письмо</Tabs.Tab>
            {letter.followups.map((step) => (
              <Tabs.Tab key={step.step} value={`step${step.step}`}>
                Добивка {step.step}
              </Tabs.Tab>
            ))}
          </Tabs.List>
        </Tabs>

        {followup !== null && next !== null ? (
          <Stack gap="xs">
            <Text size="sm" c="dimmed">
              Уйдёт сама через {followup.in_days} дн. после предыдущего письма — если адресат не
              ответит, не отпишется и письмо не вернётся отказом доставки.
            </Text>
            <Text fw={500}>{readable(followup.subject).text}</Text>
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
              {next.text}
            </Text>
            {next.unset ? <UnsetHint /> : null}
            <Text size="xs" c="dimmed">
              Текст добивки один на всю рассылку и правится шаблоном в коде: модель его не трогает.
            </Text>
          </Stack>
        ) : editing ? (
          <Stack gap="sm">
            <TextInput
              label="Тема"
              value={subject}
              onChange={(event) => setSubject(event.currentTarget.value)}
            />
            <Textarea
              label="Текст письма"
              autosize
              minRows={12}
              value={body}
              onChange={(event) => setBody(event.currentTarget.value)}
            />
            {shown.unset || title.unset ? <UnsetHint editing /> : null}
            <Text size="xs" c="dimmed">
              Проценты пересчитаются после сохранения — от шаблона, который лежит сейчас.
            </Text>
          </Stack>
        ) : (
          <Stack gap="xs">
            <Text fw={500}>{title.text}</Text>
            {/* Письмо — обычный текст, и переносы в нём значимые: абзацы
                задают его ритм, а свёрнутое в одну строку письмо читается
                иначе, чем уйдёт адресату. */}
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
              {shown.text}
            </Text>
            {shown.unset || title.unset ? <UnsetHint /> : null}
          </Stack>
        )}

        {!transportIsReal && followup === null ? (
          <Alert color="yellow" title="Наружу письмо не уйдёт">
            Почта ещё не подключена: письмо будет помечено отправленным, но адресат его не получит.
            Отправка заработает после подключения на рабочем сервере.
          </Alert>
        ) : null}

        {/* Короче, чем предупреждение наверху, и не пересказывает его:
            там сказано, почему так, здесь — почему не нажимается эта
            кнопка. Два одинаковых текста на одном экране читаются
            как сбой, а не как забота. */}
        {blocked && followup === null ? (
          <Text size="sm" c="dimmed">
            Кнопка не нажимается, пока не подключена почта — подробности наверху экрана.
          </Text>
        ) : null}

        {/* Кнопки — действие, а не третье поле: отделяются отступом.
            На вкладке добивки их нет вовсе: отправить её по кнопке
            нельзя (уйдёт по сроку), а править — значит править шаблон.

            Именно не отрисовываются, а не прячутся атрибутом `hidden`:
            у Mantine на этой группе стоит `display: flex`, и он сильнее
            умолчания `[hidden] { display: none }`. В jsdom кнопка при
            этом считается скрытой, и тест проходит — а в браузере она
            видна. Поймано снимком. */}
        {followup === null ? (
          <Group gap="sm" mt="sm">
            {editing ? (
              <>
                <Button
                  color="lagoon"
                  loading={busy}
                  className="press"
                  onClick={() =>
                    onSave(restored(subject, letter.subject), restored(body, letter.body))
                  }
                >
                  Сохранить
                </Button>
                <Button variant="subtle" color="gray" onClick={() => setEditing(false)}>
                  Отменить правку
                </Button>
              </>
            ) : (
              <>
                <Button
                  color="lagoon"
                  disabled={!canSend || blocked}
                  loading={busy}
                  className="press"
                  onClick={onSend}
                >
                  Отправить
                </Button>
                <Button
                  variant="light"
                  color="lagoon"
                  disabled={!canSend}
                  className="press"
                  onClick={() => setEditing(true)}
                >
                  Поправить
                </Button>
                <Button
                  variant="subtle"
                  color="red"
                  disabled={!canSend}
                  className="press"
                  onClick={onSkip}
                >
                  Не писать
                </Button>
              </>
            )}
          </Group>
        ) : null}

        {!canSend && followup === null ? (
          <Text size="xs" c="dimmed">
            Отправка — отдельное право, его выдают поимённо. Смотреть очередь можно всем.
          </Text>
        ) : null}
      </Stack>
    </Card>
  );
}
