/**
 * «Рассылка»: почему первые письма не уходят вовсе — плашкой над вкладкой, и когда уйдёт очередь —
 * день, дней отправки, часы.
 *
 * **Всё — из прогноза рассылки** (`GET /letters/forecast`): срок считает сервер днями плана, теми
 * же, что ниже на вкладке, а слова срока — его же (`queue_eta.words`): как посчитано, при каком
 * условии верно и почему срока нет. Экран своих слов о сроке не сочиняет.
 *
 * **Часы — только при автоотправке.** Вручную пачка выбирает дневной лимит за секунды, и срок
 * верен, если жать «Отправить очередь» каждый день отправки: так и сказано словами сервера на месте
 * часов, а плитки часов нет.
 *
 * **Письма, которые не уйдут, пока их не поправят** (`queued_stuck`: адрес держит проверка, пояс
 * получателя неизвестен), — янтарной плашкой с причинами и числами: срок и план — без них, и это
 * повод что-то сделать.
 *
 * **Автоотправка стоит** (`paused`: исход письма неизвестен, предохранитель, ни одно письмо окна не
 * уйдёт как есть) — янтарной плашкой у срока, словами сервера: срок и план посчитаны так, будто
 * остановку снимут, и это сказано рядом с ними, а не только в строке режима выше.
 *
 * **Слова срока — в «i», когда плитки уже сказали их числами** (разведка экранов 10.11.2026: строка
 * повторяла день, дни отправки и часы плиток). Есть плитки дня и часов — слова сервера уходят в «i»
 * заголовка блока, к «как считается». Нет — слова на виду, как были: вручную они говорят, при каком
 * условии срок верен, без пояса — почему нет часов, без срока — почему очередь не уходит.
 */

import { Alert, List, SimpleGrid, Stack, Text } from '@mantine/core';
import type { UseQueryResult } from '@tanstack/react-query';

import { Metric } from '../components/Metric';
import { formatNumber } from '../format';
import { Block, NoAnswer, NotRefreshed } from './MailingBlock';
import { dayLabel, hoursLabel } from './mailingData';
import type { ForecastEta, ForecastStuck, MailingForecastView } from './mailingTypes';

/** Первые письма этапа не уходят вовсе или лимит направления не прочитан — плашкой над вкладкой:
 *  после такого исхода срок и план — нули, и первое, что нужно знать, — почему. */
export function MailingBlocked({ view }: { view: MailingForecastView | undefined }) {
  const said = [view?.blocked, view?.unread_limit].filter(
    (words): words is string => typeof words === 'string',
  );
  if (said.length === 0) return null;
  return (
    <>
      {said.map((words) => (
        <Alert key={words} color="yellow" mx="md" className="mailingBlocked">
          <Text size="sm">{words}</Text>
        </Alert>
      ))}
    </>
  );
}

const ETA_HINT =
  'Срок — последнего письма, которое стоит в очереди сейчас, без писем, которые сборка ещё ' +
  'напишет, и без тех, что не уйдут, пока их не поправят. Дни — дни плана ниже: мощность ящиков ' +
  'с разгоном, окно получателя и темп автоотправки.';

function EtaTiles({ queued, eta }: { queued: number; eta: ForecastEta }) {
  return (
    <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm" px="md" className="mailingEtaTiles">
      <Metric title="В очереди" value={formatNumber(queued)} hint="первых писем" />
      {eta.day === null ? null : (
        <Metric title="Уйдёт к" value={dayLabel(eta.day)} hint="последнее письмо очереди" />
      )}
      {eta.sending_days === null ? null : (
        <Metric
          title="Дней отправки"
          value={formatNumber(eta.sending_days)}
          hint="считая сегодня"
        />
      )}
      {eta.hours === null ? null : (
        <Metric title="Часов" value={hoursLabel(eta.hours)} hint="при автоотправке" />
      )}
    </SimpleGrid>
  );
}

/** Автоотправка стоит по причине, которой прогноз сам не видит, — слова сервера с заглавной буквы и
 *  оговорка о сроке; идёт — плашки нет. */
function Paused({ words }: { words: string | null }) {
  if (words === null) return null;
  return (
    <Alert color="yellow" mx="md" className="mailingPaused">
      <Text size="sm">
        {words.charAt(0).toUpperCase() + words.slice(1)}. Пока это так, автоотправка стоит: срок и
        план — при условии, что остановку снимут.
      </Text>
    </Alert>
  );
}

/** «В очереди N, из них M не уйдут:» и причины с числами — словами сервера. */
function Stuck({ queued, stuck }: { queued: number; stuck: ForecastStuck | null }) {
  if (stuck === null || stuck.letters === 0) return null;
  return (
    <Alert color="yellow" mx="md" className="mailingStuck">
      <Text size="sm">
        В очереди {formatNumber(queued)}, из них {formatNumber(stuck.letters)} не уйдут, пока их не
        поправят:
      </Text>
      <List size="sm" mt={4} spacing={2}>
        {stuck.reasons.map((reason) => (
          <List.Item key={reason.words}>
            {reason.words} — {formatNumber(reason.letters)}
          </List.Item>
        ))}
      </List>
    </Alert>
  );
}

/** Плитки дня и часов уже сказали срок числами — слова сервера только поясняют, как он посчитан. */
function saidByTiles(eta: ForecastEta): boolean {
  return eta.day !== null && eta.hours !== null;
}

/** «Как считается» — и слова срока сервера, когда плитки уже сказали его числами. */
function etaHint(view: MailingForecastView | undefined) {
  if (view === undefined || !saidByTiles(view.queue_eta)) return ETA_HINT;
  return (
    <Stack gap={6}>
      <span>{ETA_HINT}</span>
      <span>{view.queue_eta.words}</span>
    </Stack>
  );
}

/** Когда уйдёт очередь: плитки, застрявшие письма и слова срока сервера. */
export function MailingQueue({ query }: { query: UseQueryResult<MailingForecastView> }) {
  const view = query.data;
  return (
    <Block title="Когда уйдёт очередь" hint={etaHint(view)} className="mailingEta">
      {view === undefined ? (
        <NoAnswer
          query={query}
          failed="Прогноз рассылки не загрузился"
          loading="Загружаем прогноз рассылки"
        />
      ) : (
        <>
          <NotRefreshed query={query} />
          <EtaTiles queued={view.queued_first} eta={view.queue_eta} />
          <Paused words={view.paused} />
          <Stuck queued={view.queued_first} stuck={view.queued_stuck} />
          {saidByTiles(view.queue_eta) ? null : (
            <Text size="sm" px="md" className="mailingEtaWords">
              {view.queue_eta.words}
            </Text>
          )}
        </>
      )}
    </Block>
  );
}
