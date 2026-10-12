/**
 * Последняя цена донора, откуда она — и «Указать цену», которую человек знает
 * сам (07.10.2026).
 *
 * До 07.10.2026 цена попадала к донору только из разобранного ответа на письмо,
 * а агентство знает цены многих сайтов само — свой прайс, биржи, прошлые
 * сделки. Требование Этапа 2: «по кому запускаем — только доноры с известной
 * ценой (из базы или заведённые вручную)».
 *
 * **Откуда цена — строкой под ней**: из ответа донора или вписал человек — кто
 * и откуда он её знает. На цене держится оффер «мы дешевле» Этапа 2, и цена со
 * слов коллеги должна читаться как таковая. Дата — в строке цены: у ручной это
 * и есть день, когда её вписали, и второй раз она не повторяется.
 *
 * **«Указать цену» — у донора, принятого человеком.** Кандидата и отклонённого
 * решают в очереди прогона (туда ведёт шапка карточки); домен, которого нет
 * среди доноров, заводят вручную на панели «Обход доноров». Правило записи —
 * одно с панелью и консолью: отказ — словами сервера, ничего не записано.
 * Ответ — карточка целиком, как у адреса руками: цена и её источник
 * показываются словами сервера, а не догадкой экрана.
 *
 * **Отказ по состоянию донора — до нажатия** (08.10.2026): принятый донор
 * попал в стоп-лист или в поставщики, его домен — наш домен рассылки или зона,
 * где размещений не продают. Сервер называет это в карточке (`price_refusal`)
 * словами отказа «Записать цену» — кнопка заперта, причина под ней. Прежде
 * человек вписывал цену и узнавал об отказе нажатием.
 *
 * **Цену снял ответ донора — на её месте ответ** (11.10.2026): последнее слово
 * донора побеждает, «не продаём» цену снимает, «бесплатно» ставит вместо неё
 * отметку. Карточка говорит «не продаёт» или «берёт бесплатно» словами отбора,
 * а не «цены нет»: так читалось бы «цену не спрашивали».
 *
 * **Цена целиком** (сверка ТЗ 12.10.2026: «распознанные цены (белая, серая, способы
 * оплаты) и рядом исходный текст ответа, из которого они взяты»): белая и серая, оплата,
 * срок публикации и тип ссылки, ссылка в переписку ответа, из которого цена, и сам его
 * текст — свёрнутым, по нажатию, — и «устарела», если цена старше срока: тогда сначала
 * перезапрос у донора.
 */

import { Anchor, Badge, Button, Card, Group, Stack, Text, Title } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { PERMISSION_TITLES, SELLER_ANSWERS } from '../api/labels';
import { setDonorPrice } from '../api/runs';
import type { DonorFullCard } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatDate, formatMoney, plural } from '../format';
import { Unfold } from '../runs/Unfold';
import { ReplyOffers } from '../threads/ReplyOffers';
import { NEW_PRICE, PriceForm, priceBody, priceTyped } from './PriceForm';
import type { PriceDraft } from './PriceForm';
import { notify } from '../notices';

/** Откуда последняя цена — одной строкой: «Вручную: кто · откуда цена» или
 *  «Из ответа донора». Источника нет — цена записана до 07.10.2026, когда её
 *  давал только ответ. */
function priceOrigin(donor: DonorFullCard): string {
  if (donor.last_price_source !== 'manual') return 'Из ответа донора';
  const parts = [donor.last_price_by, donor.last_price_note].filter(Boolean);
  return parts.length === 0 ? 'Вручную' : `Вручную: ${parts.join(' · ')}`;
}

/** «Из ответа донора» — ссылкой в переписку ответа, из которого цена: рядом с ним
 *  исходный текст, по которому её проверяют. Переписки нет — словами, без ссылки. */
function Origin({ donor }: { donor: DonorFullCard }) {
  const thread = donor.last_price_thread_id ?? null;
  if (donor.last_price_source === 'manual' || thread === null) {
    return (
      <Text size="sm" c="dimmed">
        {priceOrigin(donor)}
      </Text>
    );
  }
  return (
    <Anchor component={Link} to={`/threads/${thread}`} size="sm" fw={500}>
      Из ответа донора №{donor.last_price_reply_id} — в переписку
    </Anchor>
  );
}

/** Что донор написал в ответе, из которого цена, — рядом с ней, свёрнутым: по нему
 *  числа проверяют глазами. Без цитаты нашего письма; письмо целиком — в переписке. */
function ReplyText({ donor }: { donor: DonorFullCard }) {
  const [open, setOpen] = useState(false);
  const text = donor.last_price_reply_text ?? null;
  if (text === null || donor.last_price_source === 'manual') return null;
  const id = `donor-${donor.id}-reply`;
  return (
    <>
      <Button
        variant="subtle"
        size="compact-xs"
        mt={4}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen(!open)}
      >
        {open ? 'Скрыть текст ответа' : 'Показать текст ответа'}
      </Button>
      <Unfold open={open} w="100%">
        <Text id={id} size="sm" className="bubbleText" mt={4}>
          {text}
        </Text>
      </Unfold>
    </>
  );
}

/** Белая и серая цена — значками, как у ответа в переписке. Валюта у обеих одна. */
function WhiteAndGrey({ donor }: { donor: DonorFullCard }) {
  const prices = [
    ['белая', donor.last_price_white ?? null, 'green'],
    ['серая', donor.last_price_grey ?? null, 'yellow'],
  ] as const;
  const named = prices.filter(([, amount]) => amount !== null);
  if (named.length === 0) return null;
  return (
    <Group gap={6} mt={6}>
      {named.map(([title, amount, color]) => (
        <Badge key={title} variant="light" color={color}>
          {title} {formatMoney(amount, donor.last_price_currency)}
        </Badge>
      ))}
    </Group>
  );
}

/** Оплата, срок публикации и тип ссылки — строками, как их назвал донор. */
function Terms({ donor }: { donor: DonorFullCard }) {
  const methods = donor.last_payment_methods ?? [];
  const days = donor.last_placement_days ?? null;
  const link = donor.last_link_type ?? null;
  const publication = [
    days === null ? null : `${days} ${plural(days, 'день', 'дня', 'дней')}`,
    link === null ? null : `ссылка ${link}`,
  ].filter((part) => part !== null);
  return (
    <>
      {methods.length > 0 && <Text size="sm">Оплата: {methods.join(', ')}</Text>}
      {publication.length > 0 && <Text size="sm">Публикация: {publication.join(' · ')}</Text>}
    </>
  );
}

/** Срок цены: «устарела» — сначала перезапрос у донора; иначе — до какого дня свежая. */
function Freshness({ donor }: { donor: DonorFullCard }) {
  const until = donor.price_expires_at ?? null;
  if (donor.price_stale === true) {
    return (
      <Group gap={6} mt={6} wrap="nowrap" align="flex-start">
        <Badge variant="light" color="yellow">
          устарела
        </Badge>
        <Text size="sm">
          {until === null ? 'Срок цены вышел' : `Срок цены вышел ${formatDate(until)}`}: сначала
          перезапрос у донора — оффер Этапа 2 на устаревшей цене не строится.
        </Text>
      </Group>
    );
  }
  if (until === null) return null;
  return (
    <Text size="sm" c="dimmed">
      Свежая до {formatDate(until)}
    </Text>
  );
}

/** «Указать цену»: поля раскрываются кнопкой, отказ сервера — над ними. */
function SetPrice({ donor }: { donor: DonorFullCard }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<PriceDraft>(NEW_PRICE);
  const save = useMutation({
    mutationFn: () => setDonorPrice(donor.id, priceBody(draft)),
    onSuccess: (card) => {
      // Карточка — из ответа сервера; списку доноров пора перечитаться: цена
      // видна и в его строке.
      queryClient.setQueryData(['donor', String(donor.id)], card);
      void queryClient.invalidateQueries({ queryKey: ['donors'] });
      setDraft(NEW_PRICE);
      setOpen(false);
      // Донор, ставший негодным после обновления метрик, цену получает, а обход
      // его не берёт: это сказано, как у панели и консоли, — янтарём.
      const suitable = card.status === 'suitable';
      const money = formatMoney(card.last_price, card.last_price_currency);
      notify({
        message: suitable
          ? `Цена записана: ${money}.`
          : `Цена записана: ${money}. По порогам отбора донор не годен — обход Этапа 2 его не возьмёт.`,
        color: suitable ? 'green' : 'yellow',
      });
    },
  });
  const formId = `donor-${donor.id}-price`;
  // Отказ по состоянию донора — словами сервера до нажатия: вписывать цену,
  // которую всё равно не запишут, незачем, и поля не раскрываются.
  const refusal = donor.price_refusal ?? null;
  const shown = open && refusal === null;
  // Свернуть — значит отказаться от вписанного, как у окна «Завести донора
  // вручную»: после «Отмены» поля открывались с прежним 999 (проверка QA
  // 10.10.2026), и недописанная цена ждала, чтобы её записали по ошибке.
  const close = () => {
    setOpen(false);
    setDraft(NEW_PRICE);
    save.reset();
  };
  return (
    <Stack gap="sm" mt="md" align="flex-start">
      <Button
        variant="default"
        className="press"
        aria-expanded={shown}
        aria-controls={formId}
        disabled={refusal !== null}
        onClick={() => {
          if (open) {
            close();
            return;
          }
          setOpen(true);
          save.reset();
        }}
      >
        Указать цену
      </Button>
      {refusal !== null ? <Text size="sm">{refusal}</Text> : null}
      <Unfold open={shown} w="100%">
        <PriceForm
          id={formId}
          draft={draft}
          onChange={(next) => {
            setDraft(next);
            save.reset();
          }}
          ready={priceTyped(draft)}
          pending={save.isPending}
          error={save.error}
          refused="Цену не записали"
          submit="Записать цену"
          onSubmit={() => save.mutate()}
          onCancel={close}
        />
      </Unfold>
    </Stack>
  );
}

/** Ответ донора, снявший цену, — словами отбора (`SELLER_ANSWERS`): «не продаёт»,
 *  «берёт бесплатно». Последнее слово донора побеждает (11.10.2026): цены у такого
 *  донора нет, и на её месте — его ответ. `null` — цену ответ не снимал. */
function answerInstead(donor: DonorFullCard): { title: string; color: string } | null {
  const answer = donor.seller_answer ?? null;
  if (donor.last_price !== null || (answer !== 'declines' && answer !== 'free')) return null;
  return SELLER_ANSWERS[answer];
}

/** Цена с датой и откуда она — или ответ донора на её месте, или «цены нет». */
function PriceLine({ donor }: { donor: DonorFullCard }) {
  const answer = answerInstead(donor);
  if (answer !== null) {
    return (
      <>
        <Badge variant="light" color={answer.color}>
          {answer.title}
        </Badge>
        <Text size="sm" c="dimmed" mt={4}>
          Последний ответ донора — цены нет: Этап 2 по нему не запускается.
        </Text>
      </>
    );
  }
  if (donor.last_price === null) {
    return <Text size="sm">Цены нет — по донору без цены Этап 2 не запускается.</Text>;
  }
  return (
    <>
      {/* Валюта приходит с ценой, а не подставляется здесь: конвертации
          в сервисе нет, и «USD» рядом с числом в евро — это не подпись,
          а неверное число. Деньги — общей функцией, как на остальных
          экранах: сырой строкой сервера цена печаталась «250.00 EUR». */}
      <Text>
        {formatMoney(donor.last_price, donor.last_price_currency)} ·{' '}
        {formatDate(donor.last_price_at)}
      </Text>
      <Origin donor={donor} />
      <ReplyText donor={donor} />
      <WhiteAndGrey donor={donor} />
      <Terms donor={donor} />
      <Freshness donor={donor} />
    </>
  );
}

export function DonorPrice({ donor }: { donor: DonorFullCard }) {
  const { can } = useSession();
  const isDonor = donor.review === 'accepted';
  // У записи без цены, которая не донор, показывать нечего: цену ей здесь
  // не указывают, а почему — говорит шапка карточки. Ответ, снявший цену, —
  // показывать: он стоит на её месте.
  if (donor.last_price === null && answerInstead(donor) === null && !isDonor) return null;
  return (
    <Card className="glass" p="xl">
      <Title order={5} mb="xs">
        Последняя цена
      </Title>
      <PriceLine donor={donor} />
      <ReplyOffers offers={donor.last_offers} />
      {/* Без права — объяснение на месте, а не пропавшая кнопка. */}
      {isDonor && can('run') ? <SetPrice donor={donor} /> : null}
      {isDonor && !can('run') ? (
        <Text size="sm" c="dimmed" mt="sm">
          Указать цену может сотрудник с правом «{PERMISSION_TITLES.run}».
        </Text>
      ) : null}
    </Card>
  );
}
