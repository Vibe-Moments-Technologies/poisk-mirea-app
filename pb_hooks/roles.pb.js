// roles.pb.js — перенос ролевой модели из Supabase (миграции 0008/0012–0015).
//
// Роли users: super_admin | administrator | moderator | org_admin | org_member.
// Публикует автор, модераторская служба проверяет. Гварды ниже:
//   1. Деактивированный сотрудник не входит (onRecordAuthRequest).
//   2. org_admin создаёт сотрудников: роль/организация/пароль принудительно.
//   3. Роль и организацию меняет только ответственный (super/administrator),
//      super_admin/administrator трогает только super, свою роль — никто.
//   4. Статусы постов: строгий автомат (как guard_post_update),
//      rejected/withdrawn требуют moderation_note, published ставит published_at.

// Проверка суперпользователя инлайнится в каждый хендлер: PB изолирует
// верхний уровень каждого pb-файла, общие хелперы между файлами не живут.

// ---------- 1. Вход: деактивированных не пускаем ----------
onRecordAuthRequest((e) => {
  if (!e.record.getBool('is_active')) {
    throw new BadRequestError('Аккаунт деактивирован — обратитесь к руководителю.');
  }
  e.next();
}, 'users');

// ---------- 2. Создание сотрудников org_admin-ом ----------
onRecordCreateRequest((e) => {
  const col = e.record.collection().name;
  if (col !== 'users') {
    e.next();
    return;
  }
  var __isSuper = false;
  try { __isSuper = e.hasSuperuserAuth() === true; } catch (err) { __isSuper = false; }
  if (__isSuper) {
    e.next();
    return;
  }
  const actor = e.auth;
  if (!actor) {
    throw new ForbiddenError('Сотрудников создаёт ответственный.');
  }
  const actorRole = actor.getString('role');
  if (!actor.getBool('is_active')) {
    throw new ForbiddenError('Аккаунт деактивирован.');
  }
  if (actorRole === 'administrator' || actorRole === 'super_admin') {
    // Ответственный создаёт сотрудников для любой организации.
    const newRole = e.record.getString('role') || 'org_member';
    if (newRole === 'super_admin' || newRole === 'administrator') {
      throw new ForbiddenError('Роль супер-админа и администратора назначает только супер-админ.');
    }
    e.record.set('role', newRole);
    e.record.set('must_change_password', true);
    e.record.set('is_active', true);
    e.record.set('profile_completed', false);
    e.next();
    return;
  }
  if (actorRole !== 'org_admin') {
    throw new ForbiddenError('Сотрудников создаёт начальник организации или ответственный.');
  }
  // Начальник: сотрудник подчинённой роли, в организации начальника,
  // со сменой пароля при первом входе.
  e.record.set('role', 'org_member');
  e.record.set('organization', actor.getString('organization'));
  e.record.set('must_change_password', true);
  e.record.set('is_active', true);
  e.record.set('profile_completed', false);
  e.record.set('can_publish', false);
  e.record.set('can_withdraw', false);
  e.record.set('can_delete_posts', false);
  e.next();
}, 'users');

// ---------- 3. Смена роли/организации — только ответственный ----------
onRecordUpdateRequest((e) => {
  const col = e.record.collection().name;
  if (col !== 'users') {
    e.next();
    return;
  }
  // При задании нового пароля флаг «обязательно сменить» снимается —
  // иначе пользователь бесконечно попадает на тот же экран смены.
  try {
    if (e.record.getString('password') !== '') {
      e.record.set('must_change_password', false);
    }
  } catch (err) {
    /* password может быть скрыт — пропускаем */
  }
  const old = e.record.original();
  const roleChanged = e.record.getString('role') !== old.getString('role');
  const orgChanged = e.record.getString('organization') !== old.getString('organization');
  if (!roleChanged && !orgChanged) {
    e.next();
    return;
  }
  var __isSuper2 = false;
  try { __isSuper2 = e.hasSuperuserAuth() === true; } catch (err) { __isSuper2 = false; }
  if (__isSuper2) {
    e.next();
    return;
  }
  const me = e.auth;
  if (!me) {
    throw new ForbiddenError('Роль и организацию назначает только ответственный.');
  }
  const myRole = me.getString('role');
  if (myRole !== 'administrator' && myRole !== 'super_admin') {
    throw new ForbiddenError('Роль и организацию назначает только ответственный (администратор или супер-админ).');
  }
  // super_admin/administrator трогает только super (уже проверено выше).
  if (e.record.getString('role') === 'super_admin' || old.getString('role') === 'super_admin' ||
      e.record.getString('role') === 'administrator' || old.getString('role') === 'administrator') {
    throw new ForbiddenError('Роль супер-админа и администратора меняет только супер-админ.');
  }
  if (me.getString('id') === e.record.getString('id')) {
    throw new ForbiddenError('Свою роль и организацию менять нельзя.');
  }
  e.next();
}, 'users');

// ---------- 4. Автомат статусов постов ----------
onRecordCreateRequest((e) => {
  const col = e.record.collection().name;
  if (col !== 'posts') {
    e.next();
    return;
  }
  var __isSuper3 = false;
  try { __isSuper3 = e.hasSuperuserAuth() === true; } catch (err) { __isSuper3 = false; }
  if (__isSuper3) {
    e.next();
    return;
  }
  const me = e.auth;
  if (!me) {
    throw new ForbiddenError('Посты создают авторизованные сотрудники.');
  }
  // Автор — сам создатель; статус — черновик или сразу на модерацию.
  if (!e.record.getString('author')) {
    e.record.set('author', me.getString('id'));
  }
  const st = e.record.getString('status');
  if (st !== 'draft' && st !== 'pending_review') {
    throw new BadRequestError('Новый пост сохраняется как черновик или отправляется на модерацию.');
  }
  e.next();
}, 'posts');

onRecordUpdateRequest((e) => {
  const col = e.record.collection().name;
  if (col !== 'posts') {
    e.next();
    return;
  }
  var __isSuper4 = false;
  try { __isSuper4 = e.hasSuperuserAuth() === true; } catch (err) { __isSuper4 = false; }
  if (__isSuper4) {
    e.next();
    return;
  }
  const me = e.auth;
  if (!me) {
    throw new ForbiddenError('Нужен вход в систему.');
  }
  const myRole = me.getString('role');
  const old = e.record.original();
  const from = old.getString('status');
  const to = e.record.getString('status');

  if (from === to) {
    e.next();
    return;
  }

  const MOD_ROLES = ['super_admin', 'administrator', 'moderator'];
  const ORG_ROLES = ['org_admin', 'org_member'];

  if (MOD_ROLES.indexOf(myRole) !== -1) {
    // Модераторская служба: проверка очереди и снятие с публикации.
    if (from === 'pending_review' && (to === 'reviewed_ready' || to === 'rejected')) {
      if (to === 'rejected' && !e.record.getString('moderation_note')) {
        throw new BadRequestError('Укажите причину отказа (moderation_note).');
      }
      e.next();
      return;
    }
    if (to === 'withdrawn' && from === 'published' && myRole !== 'moderator') {
      if (!e.record.getString('moderation_note')) {
        throw new BadRequestError('Укажите причину снятия (moderation_note).');
      }
      e.next();
      return;
    }
    throw new BadRequestError('Модератор проверяет очередь; снятие с публикации — администратор.');
  }

  if (ORG_ROLES.indexOf(myRole) !== -1) {
    if (!me.getBool('is_active')) {
      throw new ForbiddenError('Аккаунт деактивирован.');
    }
    // чужие посты отсечены правилами (author = auth.id для member)
    const key = from + '->' + to;
    const ALLOWED = {
      'draft->pending_review': true,
      'pending_review->draft': true,
      'rejected->draft': true,
      'rejected->pending_review': true,
      'withdrawn->draft': true,
      'withdrawn->pending_review': true,
      'reviewed_ready->draft': true,
      'reviewed_ready->published': true,
      'published->draft': true,
    };
    if (!ALLOWED[key]) {
      throw new BadRequestError('Недопустимый переход статуса: ' + key);
    }
    if (to === 'published' && myRole === 'org_member' && !me.getBool('can_publish')) {
      throw new ForbiddenError('Публиковать может начальник или сотрудник с правом can_publish.');
    }
    if (to === 'published') {
      e.record.set('published_at', new Date().toISOString().replace('T', ' ').replace('Z', 'Z'));
    }
    e.next();
    return;
  }

  throw new ForbiddenError('Неизвестная роль: ' + myRole);
}, 'posts');
