"""Raw GraphQL documents and endpoint constants for the PushPress members API.

The members portal (members.pushpress.com) is a Flutter SPA that talks to a
GraphQL backend at api.pushpress.com. Auth is a bearer JWT minted by POSTing
email+password to /v2/auth/login.

This module holds only strings + constants so the transport/auth logic in
``client.py`` stays readable. Introspection is disabled on the API, so these
documents were recovered by reverse-engineering the member app.
"""

from __future__ import annotations

API_BASE = "https://api.pushpress.com"
GRAPHQL_URL = f"{API_BASE}/v2/graph/graphql"
LOGIN_URL = f"{API_BASE}/v2/auth/login"

DEFAULT_HEADERS = {
    "content-type": "application/json",
    "accept": "*/*",
    "origin": "https://members.pushpress.com",
    "referer": "https://members.pushpress.com/",
}

# CrossFit-class calendarSessionTypeId. The member SPA uses 2 for classes.
CALENDAR_SESSION_TYPE_ID = 2

QUERY_PROFILE = """
query GetProfiles($clientUuid: String!, $userUuid: String!) {
  profile: getProfile(getProfileInput: {clientUuid: $clientUuid, userUuid: $userUuid}) {
    clientUserUuid
    userUuid
    clientUuid
    firstName
    lastName
    email
    phone
    address1
    address2
    emergencyName
    emergencyPhone
    subscriptions {
      subscriptionUuid
      status
      active
      plan
      __typename
    }
    __typename
  }
  __typename
}
"""

QUERY_MEMBER_INFO = """
query GetMemberInfo($clientUuid: String!, $userUuid: String!) {
  profile: getProfile(getProfileInput: {clientUuid: $clientUuid, userUuid: $userUuid}) {
    firstName
    lastName
    email
    phone
    address1
    address2
    emergencyName
    emergencyPhone
    subscriptions {
      subscriptionUuid
      status
      active
      plan
      currentPeriodUsage {
        limit
        reservations
        checkins
        period
        periodStart
        periodEnd
        __typename
      }
      __typename
    }
    __typename
  }
  __typename
}
"""

QUERY_SUBSCRIPTION_USAGE = """
query GetSubscriptionUsage($clientUuid: String!, $userUuid: String!) {
  profile: getProfile(getProfileInput: {clientUuid: $clientUuid, userUuid: $userUuid}) {
    subscriptions {
      subscriptionUuid
      status
      active
      plan
      currentPeriodUsage {
        limit
        reservations
        checkins
        period
        periodStart
        periodEnd
        __typename
      }
      __typename
    }
    __typename
  }
  __typename
}
"""

QUERY_LOCATIONS = """
query GetLocations {
  locations: getLocations(getLocationsInput: {}) {
    uuid
    name
    isActive
    __typename
  }
  __typename
}
"""

QUERY_CLASS_TYPES = """
query GetClassTypes($date: String!) {
  classTypes: getClassTypes(getClassTypesInput: {date: $date}) {
    id
    name
    __typename
  }
  __typename
}
"""

QUERY_CLASS = """
query GetClass($uuid: String!) {
  calendarItem: getClass(getClassInput: {uuid: $uuid}) {
    uuid
    title
    startDatetime
    endDatetime
    attendanceCap
    spotsAvailable
    location { name __typename }
    mainCoach { firstName lastName __typename }
    description
    __typename
  }
  __typename
}
"""

QUERY_CLASSES = """
query GetClasses($classDate: Date!) {
  classes: getCalendarItems(getCalendarItemsInput: {startDate: $classDate, endDate: $classDate, calendarSessionTypeId: 2}) {
    uuid
    title
    attendanceCap
    spotsAvailable
    registrationStartOffset
    registrationEndOffset
    startTime: startDatetime
    endTime: endDatetime
    location { name __typename }
    mainCoach { firstName lastName __typename }
    __typename
  }
  __typename
}
"""

QUERY_RESERVATIONS = """
query GetUpcomingReservations {
  reservations: getUpcomingReservations {
    uuid
    reservationTitle
    calendarItemUuid
    isActive
    isCancelled
    rawStartTime: reservationStart
    rawEndTime: reservationEnd
    rawStatus: status
    calendarItem {
      location { name __typename }
      mainCoach { firstName lastName __typename }
      __typename
    }
    __typename
  }
  __typename
}
"""

MUTATION_BOOK = """
mutation CreateReservation($clientUserUuid: String!, $calendarItemUuid: String!, $subscriptionUuid: String!, $source: String) {
  createReservation(createReservationInput: {clientUserUuid: $clientUserUuid, calendarItemUuid: $calendarItemUuid, subscriptionUuid: $subscriptionUuid, source: $source}) {
    uuid
    __typename
  }
  __typename
}
"""

MUTATION_CANCEL = """
mutation CancelReservation($reservationId: String!) {
  cancelReservation(cancelReservationInput: {reservationId: $reservationId}) {
    uuid
    __typename
  }
  __typename
}
"""

QUERY_WORKOUT_OF_DAY = """
query GetWorkoutOfDay($classTypeUid: String!, $date: String!) {
  getWorkoutOfDay(getWorkoutOfDayInput: {classTypeUid: $classTypeUid, date: $date}) {
    uid
    workoutUid
    workoutState
    workoutProgramGroupId
    workoutProgramTemplateId
    imageUrl
    videoUrlId
    day
    parts {
      workoutPartUid
      title
      description
      scoreType
      athletesNotes
      coachesNotes
      scoreCount
      sets
      defaultReps
      __typename
    }
    __typename
  }
  __typename
}
"""

QUERY_WORKOUT_SCORES = """
query GetWorkoutScores($workoutPartUid: String!, $workoutUid: String) {
  workoutGetScores(workoutGetScoresInput: {workoutPartUid: $workoutPartUid, workoutUid: $workoutUid}) {
    scores {
      id
      date
      division
      sets { weight reps }
      mine
      athleteUid
      athleteComment
      workoutUid
      workoutPartUid
      __typename
    }
    topScore {
      id
      date
      division
      primaryScore
      secondaryScore
      sets { weight reps }
      athleteUid
      athleteComment
      __typename
    }
    __typename
  }
  __typename
}
"""
