/**
 * Письмо сборки строкой — и тексты его добивок по раскрытию.
 *
 * **Где смотрят очередь перед отправкой — здесь.** Пачка уходит кнопкой ниже, а добивки — сами,
 * по расписанию, без человека: их тексты собраны вместе с письмом (зоны rewrite шагов 2–3, у
 * каждого лида свой набор в коридоре отличия). Строка говорит коротко — отличие или «шаблоном»,
 * — а тексты и почему такой текст — по «Тексты добивок», словами сервера: какими добивки уйдут
 * сейчас, тем же правилом, что у отправки (цепочку могли поправить после сборки).
 *
 * Раскрытие — только у письма в очереди с добивками, переписанными при сборке: добивки только
 * из fixed у всех лидов одни — их видно на вкладке «Цепочка писем». Раскрывается в два такта
 * (`runs/Unfold`), закрытое не отрисовывается — и тексты спрашиваются у сервера только тогда.
 */

import { Button, Loader, Stack, Text } from '@mantine/core';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { chainStepTitle } from '../api/salesLabels';
import type { SalesFollowupCard, SalesQueueLetter } from '../api/salesTypes';
import { Unfold } from '../runs/Unfold';
import { followupsOf, letterLine, useLeadFollowups } from './queueData';

/** Добивка: шаг и как получен текст — строкой, под ней текст. Вторая — под тонкой линией:
 *  без неё её строка слипалась с последним абзацем первой. */
function Followup({ item, first }: { item: SalesFollowupCard; first: boolean }) {
  const zones = item.zones.length > 0 ? ` (переписаны ${item.zones.join(', ')})` : '';
  return (
    <Stack gap={2} {...(first ? {} : { className: 'hairline', pt: 8 })}>
      <Text size="sm" fw={500}>
        {chainStepTitle(item.step)} — {item.words}
        {zones}
      </Text>
      {item.body !== null ? (
        <Text
          size="sm"
          className="leadFollowupText"
          // Абзацы — как в письме; длинная ссылка или адрес переносятся, а не режут строку на телефоне.
          style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
        >
          {item.body}
        </Text>
      ) : null}
    </Stack>
  );
}

function LeadFollowups({ lead }: { lead: number }) {
  const followups = useLeadFollowups(lead);
  let body;
  if (followups.data !== undefined) {
    body =
      followups.data.followups.length === 0 ? (
        <Text size="sm" c="dimmed">
          Добивок впереди нет: цепочка кончилась или ушла целиком.
        </Text>
      ) : (
        <>
          {followups.data.followups.map((item, index) => (
            <Followup key={item.step} item={item} first={index === 0} />
          ))}
          <Text size="xs" c="dimmed">
            Подпись и физический адрес допишет отправка — как у первого письма.
          </Text>
        </>
      );
  } else if (followups.error) {
    body = (
      <Text size="sm" c="red">
        Добивки не загрузились: {refusalOf(followups.error)}
      </Text>
    );
  } else {
    body = <Loader size="xs" aria-label="Загружаем добивки" />;
  }
  return (
    <Stack gap={6} className="leadFollowups" aria-label="Добивки письма">
      {body}
    </Stack>
  );
}

export function BuildLetter({ letter }: { letter: SalesQueueLetter }) {
  const [open, setOpen] = useState(false);
  const texts = letter.queued && followupsOf(letter).length > 0;
  const id = `lead-followups-${letter.lead_id}`;
  return (
    <>
      {letterLine(letter)}
      {texts ? (
        <>
          {' '}
          <Button
            variant="subtle"
            size="compact-xs"
            className="press"
            aria-expanded={open}
            aria-controls={id}
            onClick={() => setOpen((shown) => !shown)}
          >
            {open ? 'Скрыть тексты добивок' : 'Тексты добивок'}
          </Button>
        </>
      ) : null}
      {texts ? (
        <Unfold open={open} mt={4}>
          <div id={id}>
            <LeadFollowups lead={letter.lead_id} />
          </div>
        </Unfold>
      ) : null}
    </>
  );
}
