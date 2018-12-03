# get object centroids, convert index to actual object name, check if object has desired affordance
#  i.e. graspable and contains

# TODO: deal with identifical objects of the same type
import os

OBJ_CLASSES = ('__background__', 'bowl', 'tvm', 'pan', 'hammer', 'knife', 'cup', 'drill', 'racket', 'spatula', 'bottle')
# list of affordances
AFF_CLASSES = ('', '', '', '', '', 'CONTAINABLE', '', '', '', 'GRASPABLE')


def write_pddl(path, objs):
    # quick fix, need to adjust later
    objects = {}

    define =  '(define (problem handy_vision)\n'
    domain =  '    (:domain handy)\n'
    objects = '    (:objects arm '
    init =    '    (:init (free arm) '
    goal =    '    (:goal (and (contains bowl cup))))'
    
    # add objects to .pddl
    for obj in objs:
        if obj[0] != 0:
            label = OBJ_CLASSES[obj[0]]
            aff = AFF_CLASSES[obj[1]]
            if label not in objects:
                objects += (label + ' ')
            if  aff:
                init += ('(' + aff + ' ' + label + ') ')

    # end strings
    objects += ')\n'
    init += ')\n'
    with open(os.path.join(path, 'auto_problem.pddl'), 'w') as f:
        f.write(define + domain + objects + init + goal)
        
        
